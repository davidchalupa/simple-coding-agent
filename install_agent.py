import curses
import os

from model_registry import MODEL_REGISTRY
from downloaders.download_gguf import download_model


def main(stdscr):
    curses.curs_set(0)  # Hide the cursor

    # Initialize model states based on existing models in ./models/
    model_states = {model: False for model in MODEL_REGISTRY}

    # Path to models directory
    MODELS_DIR = "./models/"

    def check_model_exists(model_name):
        """Returns True if model file exists, else False."""
        return os.path.exists(os.path.join(MODELS_DIR, model_name))

    # Pre-check existing models — mark with [X] if found
    for model, metadata in MODEL_REGISTRY.items():
        if check_model_exists(metadata["filename"]):
            model_states[model] = True

    # Default activation (if needed)
    model_names = list(MODEL_REGISTRY.keys())
    # You can keep "qwen2.5-7b" as default or remove it — depends on your use case
    # model_states["qwen2.5-7b"] = True  # Uncomment if you want to enforce this

    def show_current_menu(chosen_model_index):
        stdscr.clear()
        stdscr.addstr(0, 0, "Simple coding agent & coding consultant setup", curses.A_BOLD)
        stdscr.addstr(1, 0, "Press '↑' to move up, '↓' to move down, 'Space' to select a model, 'Enter' to submit setup, 'q' to quit", curses.A_DIM)

        y = 3
        cursor_y = y + chosen_model_index
        for model in MODEL_REGISTRY:
            display_name = MODEL_REGISTRY[model]["display_name"]
            checkbox = "[X]" if model_states[model] else "[ ]"

            stdscr.addstr(y, 0, f"{checkbox} {display_name}", curses.A_NORMAL)
            if cursor_y == y:
                stdscr.addstr(y, 0, f"{checkbox} {display_name}", curses.A_BOLD)
            y += 1

    chosen_model_index = 0
    while True:
        show_current_menu(chosen_model_index)

        stdscr.refresh()
        key = stdscr.getch()

        if key == ord('q'):
            break
        elif key == ord('\n'):
            active_models = [model for model, state in model_states.items() if state]
            stdscr.clear()
            stdscr.addstr(0, 0, "Setup submitted", curses.A_BOLD)
            stdscr.addstr(1, 0, f"Active models: {', '.join(active_models)}", curses.A_NORMAL)
            stdscr.addstr(2, 0, "Press 'Enter' to download models or 'q' to quit", curses.A_DIM)
            stdscr.refresh()
            while True:
                key = stdscr.getch()
                if key == ord('q'):
                    return
                elif key == ord('\n'):
                    # suspending curses for installs
                    curses.endwin()
                    print("\nStarting downloads...")
                    for model in active_models:
                        download_model(model)
                    input("\nDownloads complete. Press Enter to return to the menu...")

                    stdscr.clear()
                    stdscr.addstr(0, 0, "Models downloaded", curses.A_BOLD)
                    stdscr.addstr(1, 0, "Press 'Enter' to quit", curses.A_DIM)
                    stdscr.refresh()
                    while True:
                        key = stdscr.getch()
                        if key == ord('\n'):
                            return
        elif key == curses.KEY_DOWN:
            if chosen_model_index == len(MODEL_REGISTRY.keys()) - 1:
                continue
            chosen_model_index += 1
        elif key == curses.KEY_UP:
            if chosen_model_index == 0:
                continue
            chosen_model_index -= 1
        elif key == ord(' '):
            model = list(MODEL_REGISTRY.keys())[chosen_model_index]
            model_states[model] = not model_states[model]
            continue

curses.wrapper(main)
