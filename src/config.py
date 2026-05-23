import os
from pydantic import BaseModel

class VppConfig(BaseModel):
    """Centralized configuration for the VPP Real-Time System."""
    
    # Directories
    output_dir: str = "output"
    input_dir: str = "input"
    
    # Filenames
    processor_settings_filename: str = "processor_settings.json"
    task_set_filename: str = "task_set.json"
    price_filename: str = "price_72hr.json"
    schedule_result_filename: str = "schedule_result.json"
    
    # Magic Numbers
    horizon: int = 72
    epsilon: float = 1e-6
    
    @property
    def processor_settings_path(self) -> str:
        return os.path.join(self.input_dir, self.processor_settings_filename)
        
    @property
    def task_set_path(self) -> str:
        return os.path.join(self.output_dir, self.task_set_filename)
        
    @property
    def price_path(self) -> str:
        return os.path.join(self.input_dir, self.price_filename)
        
    @property
    def schedule_result_path(self) -> str:
        return os.path.join(self.output_dir, self.schedule_result_filename)

# Global default instance
config = VppConfig()
