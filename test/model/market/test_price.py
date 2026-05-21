import os

from src.model.market.price import PriceSystem


def test_price_system_load():
    """Test loading PriceSystem from the actual input JSON."""
    current_dir = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(current_dir, "../../../.."))
    json_path = os.path.join(project_root, "input", "price_72hr.json")
    
    price_system = PriceSystem.load_from_json(json_path)
    
    assert price_system is not None
    assert len(price_system.price) == 76
    assert price_system.price[0].hour == 1
    assert price_system.price[0].market_price == 84
    assert price_system.price[-1].hour == 72
    assert price_system.price[-1].market_price == 94
