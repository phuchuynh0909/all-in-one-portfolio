#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Decode MetaStock Fxxx.DAT to a pandas DataFrame (optionally save CSV)

- Detects record size (24 or 20 bytes) automatically.
- Converts Microsoft Binary Format (MBF) 32-bit floats to IEEE 754 floats.
- Assumes the first field in each record is a date-like numeric value.
- Optionally maps that numeric value to a calendar date using an Excel-like serial base.
- Writes CSV with columns: date, open, high, low, close, volume, raw_date, date_int

Usage:
  python decode_metastock_dat.py F42.DAT -o F42_decoded.csv
  python decode_metastock_dat.py F42.DAT -o F42_decoded.csv --no-date-map
  python decode_metastock_dat.py F42.DAT -o F42_decoded.csv --date-base 1900
  python decode_metastock_dat.py F42.DAT -o F42_decoded.csv --date-base 1904

Notes:
- If dates don’t look right, try --no-date-map (keeps numeric raw_date/date_int so you can remap later).
- True symbol names & robust dating often live in MASTER/EMASTER/XMASTER; this script works with the F*.DAT alone.
"""

import argparse
import os
import struct
import datetime
import pandas as pd

def fmsbin2ieee(data):
    """
    Convert an array of 4 bytes containing Microsoft Binary floating point
    number to IEEE floating point format (which is used by Python)
    """
    as_int = struct.unpack("i", data)
    if not as_int:
        return 0.0
    man = int(struct.unpack('H', data[2:])[0])
    if not man:
        return 0.0
    exp = (man & 0xff00) - 0x0200
    man = man & 0x7f | (man << 8) & 0x8000
    man |= exp >> 1

    data2 = bytes(data[:2])
    if type(data2) is str:
        # python2
        data2 += chr(man & 255)
        data2 += chr((man >> 8) & 255)
    else:
        # python3
        data2 += bytes([man & 255])
        data2 += bytes([(man >> 8) & 255])
    return struct.unpack("f", data2)[0]

def float2date(date):
    """
    Metastock stores date as a float number.
    Here we convert it to a python datetime.date object.
    """
    date = int(date)
    if date < 101:
        date = 101
    year = 1900 + (date // 10000)
    month = (date % 10000) // 100
    day = date % 100
    return datetime.datetime(year, month, day)

def int2date(date):
    year = (date // 10000)
    month = (date % 10000) // 100
    day = date % 100
    return datetime.datetime(year, month, day)

def float2time(time):
    """
    Metastock stores date as a float number.
    Here we convert it to a python datetime.time object.
    """
    time = int(time)
    hour = time // 10000
    minute = (time % 10000) // 100
    return datetime.time(hour, minute)

def paddedString(s, encoding):
    # decode and trim zero/space padded strings
    zeroPadding = 0
    if type(s) is str:
        #python 2
        zeroPadding = '\x00'
    end = s.find(zeroPadding)
    if end >= 0:
        s = s[:end]
    try:
        return s.decode(encoding).rstrip(' ')
    except Exception as e:
        print("Error while reading the stock name. Did you specify the correct encoding?\n" +
              "Current encoding: %s, error message: %s" % (encoding, e))
        raise
        
class Column:
    """
    This is a base class for classes reading metastock data for a specific
    columns. The read method is called when reading a decode the column
    value
    @ivar dataSize: number of bytes is the data file that is used to store
                    a single value
    @ivar name: column name
    """
    dataSize = 4
    name = None

    def __init__(self, name):
        self.name = name

    def read(self, bytes):
        """Read and return a column value"""


class DateColumn(Column):
    """A date column"""
    def read(self, bytes):
        """Convert from MBF to date string"""
        return float2date(fmsbin2ieee(bytes))

class TimeColumn(Column):
    """A time column"""
    def read(self, bytes):
        """Convert read bytes from MBF to time string"""
        return float2time(fmsbin2ieee(bytes))

class FloatColumn(Column):
    """
    A float column
    @ivar precision: round floats to n digits after the decimal point
    """
    precision = 2
    def read(self, bytes):
        """Convert bytes containing MBF to float"""
        return fmsbin2ieee(bytes)

class IntColumn(Column):
    """An integer column"""
    def read(self, bytes):
        """Convert MBF bytes to an integer"""
        return int(fmsbin2ieee(bytes))

# we map a metastock column name to an object capable reading it
knownMSColumns = {
    'date': DateColumn('Date'),
    'time': TimeColumn('Time'),
    'open': FloatColumn('Open'),
    'high': FloatColumn('High'),
    'low': FloatColumn('Low'),
    'close': FloatColumn('Close'),
    'volume': IntColumn('Volume'),
    'oi': IntColumn('Oi'),
    'index': IntColumn('Index'),
    'price': FloatColumn('Price'),
}
unknownColumnDataSize = 4    # assume unknown column data is 4 bytes long
        
def mbf32_to_float(mbf_bytes: bytes) -> float:
    """
    Convert a 4-byte Microsoft Binary Format (MBF) single to IEEE-754 float.
    MBF layout (big-endian for explanation): [exp][mantissa_hi_with_sign][mantissa_mid][mantissa_lo]
    In many MetaStock files the bytes are stored little-endian as a 32-bit word; we reverse before feeding here.
    """
    exp = mbf_bytes[0]
    b1, b2, b3 = mbf_bytes[1], mbf_bytes[2], mbf_bytes[3]
    if exp == 0 and b1 == 0 and b2 == 0 and b3 == 0:
        return 0.0
    sign = (b1 & 0x80) >> 7
    mant = ((b1 & 0x7F) << 16) | (b2 << 8) | b3
    # Shift to account for MBF's explicit 1 and bias differences
    mant <<= 1
    ieee_exp = exp - 2  # MBF bias 128 vs IEEE 127 and the extra shift
    if ieee_exp <= 0:
        return 0.0
    if ieee_exp >= 255:
        # clamp to inf-like
        ieee_exp = 255
        mant = 0
    ieee = (sign << 31) | ((ieee_exp & 0xFF) << 23) | (mant & 0x7FFFFF)
    return struct.unpack(">f", struct.pack(">I", ieee))[0]

def bytes_to_mbf_float_from_le(field_le: bytes) -> float:
    # field_le is 4 bytes as stored in file (little-endian word); reverse to MBF byte order
    return mbf32_to_float(field_le[::-1])

def detect_record_size(body_len: int) -> int:
    for cand in (28, 32):
        if body_len % cand == 0:
            return cand
    raise ValueError("Unable to detect record size (not divisible by 28 or 32).")

def parse_args():
    ap = argparse.ArgumentParser(description="Decode MetaStock Fxxx.DAT to CSV")
    ap.add_argument("input", help="Path to Fxxx.DAT file")
    ap.add_argument("-o", "--output", default=None, help="Output CSV path (default: <input>_decoded.csv)")
    ap.add_argument("--fmt", choices=["mbf32", "ieee32", "int32x100", "int32x1000"], default="ieee32",
                    help="Record field format: mbf32 (old MBF floats), ieee32 (little-endian IEEE floats), int32x100 or int32x1000 (scaled ints). Default: ieee32")
    ap.add_argument("--date-mode", choices=["excel1900", "excel1904", "yyyymmdd", "none"], default="excel1900",
                    help="Date interpretation mode. Default: excel1900")
    ap.add_argument("--excel-serial-window", default="20000,90000",
                    help="Only map serials within this inclusive range (min,max). Default '20000,90000' ~ 1955..2146")
    return ap.parse_args()

def serial_to_date(v: int, base_choice: str) -> str:
    """Map integer serial to ISO date string based on chosen base."""
    if base_choice == "1904":
        base = datetime(1904, 1, 1)
        # Excel 1904 base has no leap-year bug adjustment
        days = v
    else:
        base = datetime(1900, 1, 1)
        # Excel 1900 serial includes the 1900-02-29 bug; typical practice subtract 1 (or 2) depending on data vendor.
        # We align with common CSV exports: date = base + (serial - 2) days
        days = v - 2
    try:
        d = base + timedelta(days=days)
        return d.date().isoformat()
    except Exception:
        return ""

# def decode_header_blob(header: bytes):

def main():
    args = parse_args()
    inp = args.input
    if not os.path.exists(inp):
        raise SystemExit(f"File not found: {inp}")

    fields = 4
    if fields == 7:
        columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'oi']
    elif fields == 8:
        columns = ['date', 'time', 'price', 'volume', 'low', 'close', 'volume', 'oi']
    elif fields == 4:
        columns = ['date', 'time', 'price', 'volume']
    else:
        raise ValueError('do not know how to read this number of columns %i'%fields)        

    with open(inp, 'rb') as file_handle:
        first_rec = struct.unpack("H", file_handle.read(2))[0]
        last_rec = struct.unpack("H", file_handle.read(2))[0]
        print("First record: ", first_rec)
        print("Last record: ", last_rec)
        file_handle.seek((fields - 1) * 4, os.SEEK_CUR)    
        rows = []
        eof_reached = False
        for _ in range(last_rec*10):
            row = []
            for column in columns:
                col = knownMSColumns.get(column)
                if col is None:
                    file_handle.seek(unknownColumnDataSize, os.SEEK_CUR)
                else:
                    byte = file_handle.read(col.dataSize)
                    if not byte:
                        eof_reached = True
                        break
                    value = col.read(byte)
                    row.append(value)
            if eof_reached:
                print("EOF reached")
                break
            rows.append(row)
    df = pd.DataFrame(rows, columns=columns)
    # Build timestamp column from date & time Series in a vectorized way
    df['ts'] = pd.to_datetime(
        df['date'].dt.strftime('%Y-%m-%d') + ' ' + df['time'].astype(str),
        errors='coerce'
    )
    # If you need string format instead of datetime, uncomment:
    # df['ts'] = df['ts'].dt.strftime('%Y-%m-%d %H:%M:%S')
    ## sort by date + time
    # df = df.sort_values(by=['date', 'time'])
    print(df.head())
    
    # df.to_csv('F42_decoded.csv', index=False)
    # from tabulate import tabulate
    # print(tabulate(df, headers='keys', tablefmt='psql'))


    # for loop print 100 last rows
    # for i in range(100):
    #     print(df.iloc[-i])

    # # Prepare bounds for mapping serials to dates
    # try:
    #     lo, hi = [int(x.strip()) for x in args.excel_serial_window.split(",")]
    # except Exception:
    #     lo, hi = 20000, 90000

    # rows = []
    # for i in range(nrec):
    #     chunk = body[i * rec_len : (i + 1) * rec_len]
    #     fields = [chunk[j:j+4] for j in range(0, rec_len, 4)]

    #     # Parse fields according to format
    #     if args.fmt == "mbf32":
    #         vals = [bytes_to_mbf_float_from_le(b) for b in fields]
    #         raw_date = vals[0]
    #         if rec_len == 24:
    #             o, h, l, c, v = vals[1:6]
    #         else:
    #             o, h, l, c = vals[1:5]
    #             v = 0.0
    #         date_int = int(round(raw_date)) if raw_date == raw_date else 0
    #     elif args.fmt == "ieee32":
    #         vals = [bytes_to_ieee_float_le(b) for b in fields]
    #         raw_date = vals[0]
    #         if rec_len == 24:
    #             o, h, l, c, v = vals[1:6]
    #         else:
    #             o, h, l, c = vals[1:5]
    #             v = 0.0
    #         date_int = int(round(raw_date)) if raw_date == raw_date else 0
    #     elif args.fmt == "int32x100":
    #         vals = [bytes_to_int32_le(b) for b in fields]
    #         raw_date = vals[0]
    #         if rec_len == 24:
    #             o, h, l, c = [x / 100.0 for x in vals[1:5]]
    #             v = float(vals[5])
    #         else:
    #             o, h, l, c = [x / 100.0 for x in vals[1:5]]
    #             v = 0.0
    #         date_int = int(raw_date)
    #     elif args.fmt == "int32x1000":
    #         vals = [bytes_to_int32_le(b) for b in fields]
    #         raw_date = vals[0]
    #         if rec_len == 24:
    #             o, h, l, c = [x / 1000.0 for x in vals[1:5]]
    #             v = float(vals[5])
    #         else:
    #             o, h, l, c = [x / 1000.0 for x in vals[1:5]]
    #             v = 0.0
    #         date_int = int(raw_date)
    #     else:
    #         raise ValueError(f"Unsupported fmt: {args.fmt}")

    #     # Map date string
    #     if args.date_mode == "none":
    #         date_str = ""
    #     elif args.date_mode in ("excel1900", "excel1904"):
    #         if lo <= date_int <= hi:
    #             base = "1904" if args.date_mode == "excel1904" else "1900"
    #             date_str = serial_to_date(date_int, base)
    #         else:
    #             date_str = ""
    #     elif args.date_mode == "yyyymmdd":
    #         date_str = yyyymmdd_to_date(date_int)
    #     else:
    #         date_str = ""

    #     rows.append([date_str, o, h, l, c, v, raw_date, date_int])

    # df = pd.DataFrame(rows, columns=colnames)
    # print(df.head())


if __name__ == "__main__":
    main()