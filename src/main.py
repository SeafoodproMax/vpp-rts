import json
import os
from src.generator import TaskSetGenerator

def main():
    generator = TaskSetGenerator()
    tasks_dict, frame_size = generator.generate()
    output_data = {
        "frame_size": frame_size,
        "periodic": tasks_dict
    }
    
    os.makedirs("output", exist_ok=True)
    with open("output/task_set.json", "w") as f:
        json.dump(output_data, f, indent=4)
        
    print(f"Generated {len(tasks_dict)} periodic tasks with frame size {frame_size}")
    print("Saved to output/task_set.json")

if __name__ == '__main__':
    main()
