import pandas as pd
from datetime import datetime
from deltalake import DeltaTable
from loguru import logger
from app.core.settings import settings

class WichartReportStore:
    def get_data(self, mack: str | None = None) -> pd.DataFrame:
        """Get report list from raw_wichart_report table."""
        dt = DeltaTable(settings.wichart_report_delta_table, storage_options=settings.delta_storage_options)
        if mack:
            df = dt.to_pandas(filters=[("mack", "==", mack.upper())])
        else:
            df = dt.to_pandas()
        return df

    def get_detail(self, report_id: int) -> pd.DataFrame | None:
        """Get report detail from wichart_reports table. Creates from raw_wichart_report if not exists."""
        logger.debug(f"Getting detail for report_id={report_id}")
        dt = DeltaTable(settings.wichart_report_detail_delta_table, storage_options=settings.delta_storage_options)

        try:
            df = dt.to_pandas(filters=[("document_id", "==", str(report_id))])
            if df is None or df.empty:
                df = self._create_detail_from_raw(report_id)
                if df is None:
                    logger.warning(f"Failed to create detail from raw for report_id={report_id}")
                    return None
        except Exception as e:
            logger.warning(f"Failed to query wichart_reports for report_id={report_id}: {e}")
            # Try to create from raw_wichart_report
            df = self._create_detail_from_raw(report_id)
        
        logger.debug(f"Returning detail for report_id={report_id}")
        return df

    def _create_detail_from_raw(self, report_id: int) -> pd.DataFrame | None:
        """Create a new record in wichart_reports from raw_wichart_report data."""
        logger.debug(f"Creating detail from raw for report_id={report_id}")
        
        # Get data from raw_wichart_report
        logger.debug(f"Fetching from raw_wichart_report table: {settings.wichart_report_delta_table}")
        raw_dt = DeltaTable(settings.wichart_report_delta_table, storage_options=settings.delta_storage_options)
        raw_df = raw_dt.to_pandas(filters=[("id", "==", report_id)])
        
        if raw_df.empty:
            logger.warning(f"Report not found in raw_wichart_report: report_id={report_id}")
            return None
        
        raw_row = raw_df.iloc[0]
        now = datetime.now()
        logger.debug(f"Found raw report: stock_symbol={raw_row.get('mack')}, title={raw_row.get('tenbaocao')}")
        
        # Map raw_wichart_report fields to wichart_reports fields
        new_record = pd.DataFrame([{
            'document_id': raw_row['id'],
            'stock_symbol': raw_row.get('mack'),
            'report_title': raw_row.get('tenbaocao'),
            'pdf_url': raw_row.get('url'),
            'source': raw_row.get('nguon'),
            'report_date': raw_row.get('ngaykn'),
            'industry_research': raw_row.get('rsnganh'),
            'industry_id': raw_row.get('idnganh'),
            'report_category': raw_row.get('loaibaocao'),
            'recommendation': raw_row.get('khuyennghi'),
            'clean_content': None,
            'llm_summary': None,
            'token_count': None,
            'status': "INIT",
            'error_message': None,
            'created_at': now,
            'updated_at': now,
            'processed_at': None,
        }])
        logger.debug(f"Created new record DataFrame with columns: {list(new_record.columns)}")

        # Insert into wichart_reports using merge
        logger.debug(f"Inserting into wichart_reports table: {settings.wichart_report_detail_delta_table}")
        try:
            dt = DeltaTable(settings.wichart_report_detail_delta_table, storage_options=settings.delta_storage_options)
            (
                dt.merge(
                    source=new_record,
                    predicate="target.document_id = source.document_id",
                    source_alias="source",
                    target_alias="target",
                )
                .when_not_matched_insert_all()
                .execute()
            )
            logger.info(f"Successfully created detail record for report_id={report_id}")
        except Exception as e:
            logger.error(f"Failed to insert detail record for report_id={report_id}: {e}")
            raise
        
        return new_record

    def update_summary(self, report_id: int, summary: str) -> bool:
        """Update llm_summary field in wichart_reports table using merge."""
        logger.debug(f"Updating summary for report_id={report_id}, summary_length={len(summary)}")
        dt = DeltaTable(settings.wichart_report_detail_delta_table, storage_options=settings.delta_storage_options)
        
        # Check if record exists, create from raw if not
        existing = dt.to_pandas(filters=[("document_id", "==", str(report_id))])
        if existing.empty:
            logger.debug(f"Record not found in wichart_reports, creating from raw for report_id={report_id}")
            created = self._create_detail_from_raw(report_id)
            if created is None:
                logger.error(f"Failed to create record from raw for report_id={report_id}")
                return False
        
        # Create update data
        update_df = pd.DataFrame([{
            'document_id': report_id,
            'llm_summary': summary,
            'updated_at': datetime.now(),
        }])
        
        # Merge: update matching rows
        try:
            (
                dt.merge(
                    source=update_df,
                    predicate="target.document_id = source.document_id",
                    source_alias="source",
                    target_alias="target",
                )
                .when_matched_update({
                    "llm_summary": "source.llm_summary",
                    "updated_at": "source.updated_at",
                })
                .execute()
            )
            logger.info(f"Successfully updated summary for report_id={report_id}")
        except Exception as e:
            logger.error(f"Failed to update summary for report_id={report_id}: {e}")
            raise
        
        return True

    def sync_latest_reports(self, limit: int = 100) -> dict:
        """
        Sync latest reports from raw_wichart_report to wichart_reports.
        Returns dict with sync statistics.
        """
        logger.info(f"Starting sync of latest {limit} reports")
        
        # Get latest records from raw_wichart_report
        raw_dt = DeltaTable(settings.wichart_report_delta_table, storage_options=settings.delta_storage_options)
        raw_df = raw_dt.to_pandas()
        
        # Sort by ngaykn (report date) descending and take latest N
        raw_df = raw_df.sort_values(by='ngaykn', ascending=False).head(limit)
        raw_ids = set(raw_df['id'].tolist())
        logger.info(f"Found {len(raw_ids)} raw reports to check")
        
        # Get existing records from wichart_reports
        detail_dt = DeltaTable(settings.wichart_report_detail_delta_table, storage_options=settings.delta_storage_options)
        detail_df = detail_dt.to_pandas()
        existing_ids = set(detail_df['document_id'].astype(str).tolist())
        logger.debug(f"Found {len(existing_ids)} existing detail records")
        
        # Find missing records (in raw but not in detail)
        missing_ids = [rid for rid in raw_ids if str(rid) not in existing_ids]
        logger.info(f"Found {len(missing_ids)} missing records to sync")
        
        if not missing_ids:
            stats = {
                "total_raw": len(raw_ids),
                "existing": len(existing_ids),
                "missing": 0,
                "created": 0,
                "failed": 0,
            }
            logger.info(f"Sync completed (no new records): {stats}")
            return stats
        
        # Filter raw_df to only missing records and transform to detail format
        missing_df = raw_df[raw_df['id'].isin(missing_ids)].copy()
        now = datetime.now()
        
        # Map raw_wichart_report fields to wichart_reports fields (bulk)
        new_records = pd.DataFrame({
            'document_id': missing_df['id'],
            'stock_symbol': missing_df.get('mack'),
            'report_title': missing_df.get('tenbaocao'),
            'pdf_url': missing_df.get('url'),
            'source': missing_df.get('nguon'),
            'report_date': missing_df.get('ngaykn'),
            'industry_research': missing_df.get('rsnganh'),
            'industry_id': missing_df.get('idnganh'),
            'report_category': missing_df.get('loaibaocao'),
            'recommendation': missing_df.get('khuyennghi'),
            'clean_content': None,
            'llm_summary': None,
            'token_count': None,
            'status': "INIT",
            'error_message': None,
            'created_at': now,
            'updated_at': now,
            'processed_at': None,
        })
        
        logger.debug(f"Prepared {len(new_records)} records for bulk insert")
        
        # Bulk insert using merge
        created = 0
        failed = 0
        try:
            (
                detail_dt.merge(
                    source=new_records,
                    predicate="target.document_id = source.document_id",
                    source_alias="source",
                    target_alias="target",
                )
                .when_not_matched_insert_all()
                .execute()
            )
            created = len(new_records)
            logger.info(f"Successfully bulk inserted {created} records")
        except Exception as e:
            logger.error(f"Bulk insert failed: {e}")
            failed = len(missing_ids)
        
        stats = {
            "total_raw": len(raw_ids),
            "existing": len(existing_ids),
            "missing": len(missing_ids),
            "created": created,
            "failed": failed,
        }
        logger.info(f"Sync completed: {stats}")
        return stats
