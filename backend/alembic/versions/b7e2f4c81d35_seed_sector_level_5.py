"""seed_sector_level_5

Seeds the sieucophieu ``stock_lists`` taxonomy as sector level 5. The ids are
that API's ``stock_list`` ids, so they stay stable against it; VN30 (23) is
excluded because it is an index basket rather than a sector.

Upsert rather than ``bulk_insert``: the sector primary key is (id, level), and a
plain insert would fail the second time this runs against a database that
already has the rows. Names are refreshed on conflict so a rename upstream can
be replayed by re-running the migration.

Revision ID: b7e2f4c81d35
Revises: d5a91c3e7b20
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services.sector_lists import LEVEL5_SECTORS, SECTOR_LEVEL_5

# revision identifiers, used by Alembic.
revision: str = 'b7e2f4c81d35'
down_revision: Union[str, None] = 'd5a91c3e7b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    statement = sa.text(
        "INSERT INTO sector (id, level, name) VALUES (:id, :level, :name) "
        "ON DUPLICATE KEY UPDATE name = VALUES(name)"
    )
    for sector_id, name in LEVEL5_SECTORS.items():
        connection.execute(statement, {"id": sector_id, "level": SECTOR_LEVEL_5, "name": name})


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM sector WHERE level = :level"), {"level": SECTOR_LEVEL_5}
    )
