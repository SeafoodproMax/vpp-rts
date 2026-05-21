import os

from src.model.asset.processor_settings import ProcessorSettingsSystem


def test_processor_settings_load():
    """Test loading ProcessorSettingsSystem from the actual input JSON."""
    current_dir = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(current_dir, "../../../.."))
    json_path = os.path.join(project_root, "input", "processor_settings.json")
    
    settings = ProcessorSettingsSystem.load_from_json(json_path)
    
    assert settings is not None
    assert len(settings.generators) == 2
    assert settings.generators[0].generator_id == "thermal_1"
    
    assert len(settings.storages) == 2
    assert settings.storages[0].storage_id == "battery_1"
    
    assert len(settings.renewable_capacities) == 2
    assert settings.renewable_capacities[0].renewable_id == "pv_1"
    
    assert len(settings.renewable_forecasts) == 2
    assert settings.renewable_forecasts[0].renewable_id == "pv_1"
    assert len(settings.renewable_forecasts[0].forecasts) == 72
    
    assert len(settings.charging_jobs) == 2
    assert settings.charging_jobs[0].job_id == "battery_1_chg"
