from dataclasses import dataclass


@dataclass
class Generator:
    """Represents a thermal power generator and its operational constraints."""
    generator_id: str
    output_min: int
    output_max: int
    ramp_up_rate: int
    ramp_down_rate: int
    min_up_time: int
    min_down_time: int
    cost_fixed: int
    cost_variable: int
    initial_on_time: int
    initial_off_time: int
    initial_energy: int
