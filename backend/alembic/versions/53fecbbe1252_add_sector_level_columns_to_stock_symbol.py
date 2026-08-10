"""add_sector_level_columns_to_stock_symbol

Revision ID: 53fecbbe1252
Revises: 9d471c5cfaba
Create Date: 2025-09-19 13:00:28.077601

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53fecbbe1252'
down_revision: Union[str, None] = '9d471c5cfaba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Define sectors with their level IDs and symbols
SECTORS_DATA = {
    (1, 500): ['BSR', 'DMS', 'MTS', 'OIL', 'PEQ', 'PLX', 'POB', 'POS', 'PPT', 'PSH', 'PTV', 'PVB', 'PVC', 'PVD', 'PVE', 'PVO', 'PVS', 'PVY', 'SHE', 'TDG', 'TLP'],  # Oil & Gas
    (1000, 1300): ['DPM', 'DCM', 'DGC', 'GVR', 'VTZ', 'AAA', 'PLC', 'DDV', 'PGN', 'CSV', 'DRI', 'TSC', 'QBS', 'DPR', 'PCH', 'LTG', 'ABS', 'LAS', 'NHH', 'BFC', 'PHR', 'TRC'],  # Materials/Chemicals
    (1000, 1700): ['HPG', 'NKG', 'HSG', 'POM', 'KSB', 'MZG', 'SMC', 'AAH', 'MSR', 'DHC', 'ACM', 'VGS', 'TVN', 'BKG', 'TLH', 'KVC', 'ATG', 'KHD', 'PAS', 'VPG', 'LCM'],  # Materials/Basic Resources
    (2000, 2300): ['CII', 'VCG', 'HUT', 'HHV', 'DPG', 'CTD', 'VGC', 'PC1', 'FCN', 'CTR', 'LCG', 'CDC', 'CTI', 'VNE', 'EVG', 'HBC', 'NTP', 'HTN', 'CRC', 'NHA', 'L14', 'VCS', 'S99', 'MST', 'C4G', 'HTI', 'HT1', 'VC7', 'PTB', 'DC4', 'C69', 'BMP', 'NNC', 'DHA', 'G36', 'THG'],  # Industrials/Construction
    (2000, 2700): ['GEX', 'VSC', 'GEE', 'GMD', 'HAH', 'VTP', 'ACV', 'PVT', 'VEA', 'VOS', 'SCS', 'TV2', 'NAG', 'TNI', 'PAC', 'DLG', 'TDP', 'TOS', 'VIP', 'DXP', 'TCO', 'CLL', 'DL1', 'VTO', 'TCL', 'TCD', 'VHG', 'SGN', 'BTH', 'PHP'],  # Industrials/Goods & Services
    (3000, 3500): ['HHS', 'CSM', 'CTF', 'HAX', 'DRC', 'GGG', 'VKC', 'SRC'],  # Consumer/Food & Beverages
    (3000, 3700): ['PNJ', 'TCM', 'TNG', 'MSH', 'TTF', 'VGT', 'TLG', 'SHI', 'GIL', 'NET', 'ACG', 'STK', 'MBG', 'GDT', 'HTG', 'RAL', 'LIX'],  # Consumer/Personal & Household
    (4000, 4500): ['DCL', 'JVC', 'TNH', 'PMC', 'FIT', 'DHG', 'DBD', 'VDP', 'DVM', 'DTP', 'DMC', 'BCP', 'IMP', 'CVN', 'DP3', 'DP1'],  # Healthcare
    (5000, 5300): ['MWG', 'FRT', 'DGW', 'PET', 'CEN', 'PSD', 'COM', 'AFX', 'ABR', 'AAT', 'TTH', 'AST'],  # Consumer Services/Retail
    (5000, 5500): ['YEG', 'HTP', 'VEF', 'ODE', 'EID', 'DST', 'STH', 'SED', 'VNB', 'ADG'],  # Consumer Services/Media
    (5000, 5700): ['VJC', 'HVN', 'VPL', 'DAH', 'SKG', 'VTD', 'VTR', 'OCH', 'RIC', 'DSN', 'MAS', 'TCT', 'TTT', 'VNS', 'TSD', 'VNG'],  # Consumer Services/Travel
    (6000, 6500): ['FOX', 'TTN', 'FOC', 'ABC'],  # Telecommunications
    (7000, 7500): ['POW', 'NT2', 'GAS', 'REE', 'GEG', 'BWE', 'SJD', 'NBP', 'BGE', 'PPC', 'QTP', 'TTA', 'TDW', 'NED'],  # Utilities
    (8000, 8300): ['SHB', 'VPB', 'STB', 'VCB', 'TCB', 'CTG', 'MBB', 'ACB', 'HDB', 'BID', 'TPB', 'EIB', 'VIB', 'LPB', 'MSB', 'EVF', 'SSB', 'OCB', 'NAB', 'ABB', 'BVB', 'KLB', 'VAB', 'NVB', 'VBB', 'PGB', 'TIN', 'SGB', 'BAB'],  # Financials/Banking
    (8000, 8500): ['BVH', 'BMI', 'BIC', 'PVI', 'MIG', 'ABI', 'VNR', 'PGI', 'PTI', 'AIC', 'PRE', 'BLI', 'BHI'],  # Financials/Insurance
    (8000, 8600): ['VIC', 'VHM', 'DXG', 'DIG', 'PDR', 'NVL', 'CEO', 'VRE', 'KBC', 'VPI', 'TCH', 'HDG', 'SCR', 'HDC', 'KDH', 'NLG', 'QCG', 'KHG', 'IDC', 'DXS', 'IJC', 'SIP', 'TAL', 'VC3', 'SZC', 'NTL', 'KOS', 'BCM', 'LDG', 'HQC', 'SGR', 'TDC'],  # Financials/Real Estate
    (8000, 8700): ['VIX', 'SSI', 'VND', 'SHS', 'VCI', 'HCM', 'MBS', 'FTS', 'AAS', 'ORS', 'BSI', 'DSE', 'VDS', 'VFS', 'CTS', 'AGR', 'BVS', 'IPA', 'APS', 'SBS', 'TVS', 'TCI', 'TVC', 'APG', 'BMS', 'EVS', 'DSC', 'IVS', 'F88'],  # Financials/Services
    (9000, 9500): ['FPT', 'CMG', 'ELC', 'VGI', 'SAM', 'ICT', 'SRA', 'ST8', 'MFS', 'ITD', 'SBD', 'POT', 'UNI'],  # Technology
}


def upgrade() -> None:
    connection = op.get_bind()
    
    # Update all symbols with their sector assignments
    for (level_1_id, level_2_id), symbols in SECTORS_DATA.items():
        for symbol in symbols:
            connection.execute(
                sa.text("UPDATE stock_symbol SET id_sector_level_1 = :l1, id_sector_level_2 = :l2 WHERE symbol = :symbol"),
                {'l1': level_1_id, 'l2': level_2_id, 'symbol': symbol}
            )


def downgrade() -> None:
    connection = op.get_bind()
    
    # Reset all symbol sector assignments
    all_symbols = [symbol for symbols in SECTORS_DATA.values() for symbol in symbols]
    for symbol in all_symbols:
        connection.execute(
            sa.text("UPDATE stock_symbol SET id_sector_level_1 = NULL, id_sector_level_2 = NULL WHERE symbol = :symbol"),
            {'symbol': symbol}
        )
    
    # Drop the columns
    op.drop_column('stock_symbol', 'id_sector_level_2')
    op.drop_column('stock_symbol', 'id_sector_level_1')
