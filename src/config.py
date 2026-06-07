import os
from typing import Optional
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
    
    """Demo 時助教會把此檔放在 input/ 資料夾內，以讀檔方式匯入。"""
    demo_jobs_filename: str = "aperiodic_n_sporadic.json"

    # Level 2 (advanced dynamic scheduling) artifacts.
    runtime_config_filename: str = "runtime_config.json"
    schedule_result_dynamic_filename: str = "schedule_result_dynamic.json"
    evaluation_results_dynamic_filename: str = "evaluation_results_dynamic.json"
    acceptance_test_log_dynamic_filename: str = "acceptance_test_log_dynamic.json"
    dynamic_run_log_filename: str = "dynamic_run_log.json"

    # Magic Numbers
    horizon: int = 72
    epsilon: float = 1e-6
    # Seed for the periodic task-set generator.
    # None  → 每次執行都產生不同的任務集（隨機）
    # 整數  → 固定任務集，每次結果完全相同（適合 demo 或報告時使用）
    # 範例：task_seed: int = 20260526
    task_seed: Optional[int] = None
    
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
        # 助教要求放在 input/ 資料夾，與 processor_settings.json 同一層
        return os.path.join(self.input_dir, self.demo_jobs_filename)

    @property
    def runtime_config_path(self) -> str:
        return self.runtime_config_filename

    @property
    def schedule_result_dynamic_path(self) -> str:
        return os.path.join(self.output_dir, self.schedule_result_dynamic_filename)

    @property
    def evaluation_results_dynamic_path(self) -> str:
        return os.path.join(self.output_dir, self.evaluation_results_dynamic_filename)

    @property
    def acceptance_test_log_dynamic_path(self) -> str:
        return os.path.join(self.output_dir, self.acceptance_test_log_dynamic_filename)

    @property
    def dynamic_run_log_path(self) -> str:
        return os.path.join(self.output_dir, self.dynamic_run_log_filename)

# Global default instance
config = VppConfig()
