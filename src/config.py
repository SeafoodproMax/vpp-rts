import os
from pydantic import BaseModel

class VppConfig(BaseModel):
    """Centralized configuration for the VPP Real-Time System."""
    
    # Directories
    output_dir: str = "output"
    input_dir: str = "input"
    references_dir: str = "references"

    # Filenames
    processor_settings_filename: str = "processor_settings.json"
    task_set_filename: str = "task_set.json"
    price_filename: str = "price_72hr.json"
    schedule_result_filename: str = "schedule_result.json"
    evaluation_results_filename: str = "evaluation_results.json"
    acceptance_test_log_filename: str = "acceptance_test_log.json"
    # Demo-provided sporadic / aperiodic jobs merged into the generated task set.
    demo_jobs_filename: str = "aperiodic_n_sporadic_template.json"

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

    @property
    def evaluation_results_path(self) -> str:
        return os.path.join(self.output_dir, self.evaluation_results_filename)

    @property
    def acceptance_test_log_path(self) -> str:
        return os.path.join(self.output_dir, self.acceptance_test_log_filename)

    @property
    def demo_jobs_path(self) -> str:
        return os.path.join(self.references_dir, self.demo_jobs_filename)

# Global default instance
config = VppConfig()
