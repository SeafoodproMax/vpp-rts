import os
from src.generator import TaskSetGenerator
from src.utils import JsonIO

def main():
    generator = TaskSetGenerator()
    tasks_dict, frame_size = generator.generate()
    output_data = {
        "frame_size": frame_size,
        "periodic": tasks_dict
    }
    
    filepath = os.path.join("output", "task_set.json")
    JsonIO.save(output_data, filepath)
        
    print(f"Generated {len(tasks_dict)} periodic tasks with frame size {frame_size}")
    print(f"Saved to {filepath}")

if __name__ == '__main__':
    main()
