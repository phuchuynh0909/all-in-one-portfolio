from unittest.mock import Mock

import pytest

from app.services.financial_data_importer import FinancialDataImporter


def _result(*, scalar=None, rows=None):
    result = Mock()
    result.scalar.return_value = scalar
    result.fetchall.return_value = rows or []
    return result


@pytest.mark.real_auth
def test_establish_item_hierarchy_supports_levels_deeper_than_five():
    db = Mock()
    importer = FinancialDataImporter(db)
    items = [
        {"key": f"level-{level}", "level": level}
        for level in range(1, 7)
    ]
    item_mapping = {
        item["key"]: index
        for index, item in enumerate(items, start=101)
    }

    importer._establish_item_hierarchy(1, items, item_mapping)

    assert db.execute.call_count == 1
    assert db.execute.call_args.args[1]["parent_id_5"] == 105
    assert db.execute.call_args.args[1]["item_id_5"] == 106


@pytest.mark.real_auth
def test_insert_statement_items_uses_three_database_round_trips():
    db = Mock()
    next_id = iter(range(101, 105))

    def execute(statement, _params=None):
        sql = str(statement)
        if "SELECT LAST_INSERT_ID()" in sql:
            return _result(scalar=next(next_id))
        if "SELECT item_key, item_id" in sql:
            return _result(rows=[(f"item-{index}", 100 + index) for index in range(1, 5)])
        return _result()

    db.execute.side_effect = execute
    importer = FinancialDataImporter(db)
    items = [
        {"key": f"item-{index}", "title": f"Item {index}", "level": index}
        for index in range(1, 5)
    ]

    mapping = importer.insert_statement_items(7, items, [])

    assert mapping == {f"item-{index}": 100 + index for index in range(1, 5)}
    assert db.execute.call_count == 3


@pytest.mark.real_auth
def test_insert_item_values_uses_one_database_round_trip():
    db = Mock()
    importer = FinancialDataImporter(db)
    items = [
        {
            "key": f"item-{item_index}",
            **{f"value{period_index}": item_index * period_index for period_index in range(1, 5)},
        }
        for item_index in range(1, 4)
    ]

    importer.insert_item_values(
        company_id=9,
        item_mapping={f"item-{index}": 100 + index for index in range(1, 4)},
        period_mapping={f"Q{index}": 200 + index for index in range(1, 5)},
        items=items,
        time_labels=[f"Q{index}" for index in range(1, 5)],
    )

    assert db.execute.call_count == 1
    assert len(db.execute.call_args.args[1]) == 12
