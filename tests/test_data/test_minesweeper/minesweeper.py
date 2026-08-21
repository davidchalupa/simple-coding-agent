import random
from collections import deque

from argparse import ArgumentParser

from common import neighbors

from action_interactive import interactive_get_action
from action_ai_agent import ai_get_action
from action_ai_agent import dfs_get_action

board_size = 9
mines_count = 12


def place_mines(first_r, first_c):
    """
    Places mines after the first cell is chosen.
    """
    # exclude the first cell and its neighbours so the first click is safe
    excluded = {(first_r, first_c)} | set(neighbors(board_size, first_r, first_c))
    all_positions = [(r, c) for r in range(board_size) for c in range(board_size) if (r, c) not in excluded]
    mines = set(random.sample(all_positions, mines_count))
    return mines

def compute_counts(mines):
    counts = [[0] * board_size for _ in range(board_size)]
    for (r, c) in mines:
        for rr, cc in neighbors(board_size, r, c):
            counts[rr][cc] += 1
    return counts

def print_full_board(mines, counts):
    # rint column headers
    col_nums = '   ' + ' '.join(str(c+1) for c in range(board_size))
    print(col_nums)
    print('  +' + '--' * board_size + '+')
    for r in range(board_size):
        row_str = f"{r+1:2}|"
        for c in range(board_size):
            if (r, c) in mines:
                row_str += 'X '
            else:
                # show the neighbor mine count (0 represented as space)
                if counts[r][c] > 0:
                    row_str += f"{counts[r][c]} "
                else:
                    row_str += f"  "
        row_str += '|'
        print(row_str)
    print('  +' + '--' * board_size + '+')

def print_board(mines, counts, revealed, flags, reveal_all=False):
    """
    Print the current board state.
    - reveal_all: if True, show mines and all numbers (final reveal)
    - '.' means unexplored cells, 'F' for flags, numbers or space (=0) for revealed cells
    """
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    FG = {
        "black": "\033[30m",
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "white": "\033[37m",
        "bright_black": "\033[90m",
        "bright_red": "\033[91m",
        "bright_green": "\033[92m",
        "bright_yellow": "\033[93m",
        "bright_blue": "\033[94m",
        "bright_magenta": "\033[95m",
        "bright_cyan": "\033[96m",
        "bright_white": "\033[97m",
    }

    # mapping for numbers -> color
    num_color = {
        1: FG["blue"],
        2: FG["green"],
        3: FG["red"],
        4: FG["magenta"],
        5: FG["yellow"],
        6: FG["cyan"],
        7: FG["white"],
        8: FG["bright_black"],
    }

    # column header (colored)
    col_nums = '   ' + ' '.join(str(c+1) for c in range(board_size))
    print(FG["cyan"] + col_nums + RESET)
    print(FG["cyan"] + '  +' + '--' * board_size + '+' + RESET)

    for r in range(board_size):
        row_str = f"{r+1:2}|"
        for c in range(board_size):
            cell_str = ""
            if reveal_all:
                if (r, c) in mines:
                    # mine in bright red, bold
                    cell_str = BOLD + FG["bright_red"] + 'X' + RESET + " "
                else:
                    v = counts[r][c]
                    if v == 0:
                        cell_str = "  "
                    else:
                        color = num_color.get(v, FG["white"])
                        cell_str = color + str(v) + RESET + " "
            else:
                if (r, c) in flags:
                    # flagged cell in yellow, bold
                    cell_str = BOLD + FG["yellow"] + 'F' + RESET + " "
                elif revealed[r][c]:
                    v = counts[r][c]
                    if v == 0:
                        # revealed empty
                        cell_str = "  "
                    else:
                        color = num_color.get(v, FG["white"])
                        cell_str = color + str(v) + RESET + " "
                else:
                    # covered cell shown as dim dot to keep '.' appearance but subdued
                    cell_str = DIM + FG["bright_black"] + '.' + RESET + " "
            row_str += cell_str
        row_str += '|'
        print(row_str)
    print(FG["cyan"] + '  +' + '--' * board_size + '+' + RESET)

def handle_click(r, c, counts, mines, revealed, flags):
    """
    Handle a click (uncover) on the cell (r, c).
    If the cell's neighbor count is 0, an iterative flood-fill with BFS is needed to reveal
    all connected zero-cells and their numbered neighbors.

    :param r, c: row and column indices
    :param counts: neighbor-mine counts matrix
    :param revealed: 2D list marking revealed cells (modified here)
    :param flags: set of flagged cells (coordinates as tuples)
    :return: True if the click was safe, False if a mine was clicked (game over)
    """
    if (r, c) in flags:
        # clicking a flagged cell does nothing - must be unflagged first
        return True

    if (r, c) in mines:
        return False  # clicked a mine -> lose

    # iterative BFS flood-fill for zeros
    q = deque()
    q.append((r, c))
    while q:
        rr, cc = q.popleft()
        if revealed[rr][cc]:
            continue
        # revealing the corresponding cell
        revealed[rr][cc] = True
        if counts[rr][cc] == 0:
            for nr, nc in neighbors(board_size, rr, cc):
                if not revealed[nr][nc] and (nr, nc) not in flags:
                    q.append((nr, nc))

    return True

def prompt_first_click():
    """
    Prompt the user for the initial first click (row, col), validating input.
    """
    while True:
        try:
            user = input("Enter first cell to uncover as 'row col' (1-9), e.g. '3 5': ").strip()
            parts = user.split()
            if len(parts) != 2:
                raise ValueError
            r_in, c_in = int(parts[0]), int(parts[1])
            if not (1 <= r_in <= board_size and 1 <= c_in <= board_size):
                raise ValueError
            return r_in - 1, c_in - 1
        except ValueError:
            print("Invalid input. Please enter two numbers between 1 and 9 separated by space.")

def run_game_loop(mines, counts, revealed, flags, get_action):
    """
    The main game loop.

    The 'get_action' parameter is a callable that takes (revealed, flags) and returns
    an action tuple: (action, r, c) where action is 'c', 'f', or 'q'.
    Can be used for both interactive mode and an automated AI mode.
    """
    # the main game loop
    while True:
        print("\nCurrent board ('.' = covered, 'F' = flag):\n")
        print_board(mines, counts, revealed, flags, reveal_all=False)

        # checking the win condition
        revealed_count = sum(1 for r in range(board_size) for c in range(board_size) if revealed[r][c])
        total_to_reveal = board_size * board_size - mines_count
        if revealed_count >= total_to_reveal:
            print("\nCongratulations - all safe cells revealed! You win!")
            print("\nFinal board (revealed):\n")
            print_board(mines, counts, revealed, flags, reveal_all=True)
            break

        action, r, c = get_action(board_size, revealed, flags, counts)
        if action == 'q':
            print("Quitting. Bye!")
            break

        if action == 'f':
            # toggle flag
            if revealed[r][c]:
                print("Cannot flag an already revealed cell.")
            else:
                if (r, c) in flags:
                    flags.remove((r, c))
                    print(f"Removed flag at ({r+1}, {c+1}).")
                else:
                    flags.add((r, c))
                    print(f"Placed flag at ({r+1}, {c+1}).")
            # continue game loop (no immediate win/lose from flag alone)
            continue

        elif action == 'c':
            if revealed[r][c]:
                print("Cell already revealed.")
                continue
            safe = handle_click(r, c, counts, mines, revealed, flags)
            if not safe:
                print("\nBOOM — you clicked a mine! Game over.\n")
                print_board(mines, counts, revealed, flags, reveal_all=True)
                break
            else:
                # safe click; loop will check for win on next iteration
                continue
        else:
            print("Unknown action. Use 'c' to click or 'f' to flag.")
            continue

def main_interactive():
    print(f"Minesweeper (CLI) - interactive mode\n  {board_size}x{board_size}, {mines_count} mines")

    # get the first click from the user - only then the mines are placed
    first_r, first_c = prompt_first_click()

    mines = place_mines(first_r, first_c)
    counts = compute_counts(mines)

    # game state trackers
    revealed = [[False] * board_size for _ in range(board_size)]
    flags = set()

    # reveal the first cell (and flood-fill zeros)
    safe = handle_click(first_r, first_c, counts, mines, revealed, flags)
    assert safe, "First click should never be a mine due to placement rules."

    # run the main loop, injecting the interactive action provider
    run_game_loop(mines, counts, revealed, flags, get_action=interactive_get_action)

def main_ai_agent(random_first_click=False):
    print(f"Minesweeper (CLI) - AI mode\n  {board_size}x{board_size}, {mines_count} mines")

    # first "click" before placing the mines
    if random_first_click:
        # random choice
        first_r = random.randrange(board_size)
        first_c = random.randrange(board_size)
    else:
        # middle cell
        first_r = int(board_size / 2)
        first_c = int(board_size / 2)

    mines = place_mines(first_r, first_c)
    counts = compute_counts(mines)

    # game state trackers
    revealed = [[False] * board_size for _ in range(board_size)]
    flags = set()

    # reveal the first cell (and flood-fill zeros)
    safe = handle_click(first_r, first_c, counts, mines, revealed, flags)
    assert safe, "First click should never be a mine due to placement rules."

    # run the main loop, injecting the interactive action provider
    run_game_loop(mines, counts, revealed, flags, get_action=ai_get_action)

def main_dfs(random_first_click=False):
    print(f"Minesweeper (CLI) - AI mode (DFS)\n  {board_size}x{board_size}, {mines_count} mines")

    # first "click" before placing the mines
    if random_first_click:
        # random choice
        first_r = random.randrange(board_size)
        first_c = random.randrange(board_size)
    else:
        # middle cell
        first_r = int(board_size / 2)
        first_c = int(board_size / 2)

    mines = place_mines(first_r, first_c)
    counts = compute_counts(mines)

    # game state trackers
    revealed = [[False] * board_size for _ in range(board_size)]
    flags = set()

    # reveal the first cell (and flood-fill zeros)
    safe = handle_click(first_r, first_c, counts, mines, revealed, flags)
    assert safe, "First click should never be a mine due to placement rules."

    # run the main loop, injecting the interactive action provider
    run_game_loop(mines, counts, revealed, flags, get_action=dfs_get_action)

if __name__ == "__main__":
    parser = ArgumentParser(
        prog='minesweeper',
        description='A CLI version of Minesweeper game with a possibility of AI play')

    parser.add_argument('-a', '--agent',
                        action='store_true',
                        help='Switches on an AI agent mode (rule-based) that plays the game automatically')
    parser.add_argument('-d', '--dfs',
                        action='store_true',
                        help='Switches on DFS search mode that plays the game automatically (full scan of options)')

    args = parser.parse_args()

    if args.agent:
        main_ai_agent()
    elif args.dfs:
        main_dfs()
    else:
        main_interactive()
