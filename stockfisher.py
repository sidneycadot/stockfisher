#! /usr/bin/env -S python3 -u

import os
import argparse
import shutil
import subprocess
import random
import time
import contextlib
from enum import Enum
from typing import Optional

try:
    import colorama
except ModuleNotFoundError:
    colorama = None

class Board:
    """Represents a chess board."""

    def __init__(self):
        """Initialize as empty board."""
        self.board = [' ' for i in range(64)]

    def mk_empty(self) -> None:
        """Remove all pieces from the board."""
        self.board = [' ' for i in range(64)]

    def mk_initial(self) -> None:
        """Set up initial pieces."""
        self.board = ['r', 'n', 'b', 'q', 'k', 'b', 'n', 'r',
                      'p', 'p', 'p', 'p', 'p', 'p', 'p', 'p',
                      ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ',
                      ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ',
                      ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ',
                      ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ',
                      'P', 'P', 'P', 'P', 'P', 'P', 'P', 'P',
                      'R', 'N', 'B', 'Q', 'K', 'B', 'N', 'R']

    def place_random_pieces(self, pieces: str) -> None:
        """Place a bunch of pieces randomly on the board."""
        empty_positions = set(i for i in range(64) if self.board[i] == ' ')
        valid_pawn_positions = set(range(8, 56))
        for piece in pieces:            
            if piece in ('p', 'P'):
                candidate_positions = (empty_positions & valid_pawn_positions)
            else:
                candidate_positions = empty_positions

            pos = random.choice(list(candidate_positions))
            self.board[pos] = piece
            empty_positions.remove(pos)

    def print_board(self) -> None:
        for y in range(8):
            for x in range(8):
                f = self.board[y * 8 + x]
                if f == ' ':
                    f = '.'
                print(f, end='')
            print()

    def fen(self, mover: str) -> str:
        """Represent the board as a FEN string.
        
        We assume that castling is not possible.
        """
        if mover not in ("w", "b"):
            raise ValueError()

        ranks = []
        for y in range(8):
            rank = []
            for x in range(8):
                f = self.board[y * 8 + x]
                if f == ' ':
                    if len(rank) != 0 and rank[-1] in "1234567":
                        pawn_count = int(rank.pop()) + 1
                    else:
                        pawn_count = 1
                    rank.append(str(pawn_count))
                else:
                    rank.append(f)
            ranks.append("".join(rank))
        fen_board = "/".join(ranks)
        return "{} {} - - 0 1".format(fen_board, mover)


class StockfishException(Exception):
    """Represents an exception while talking to Stockfish."""
    pass


class StockfishStatus(Enum):
    """Represents the status of the Stockfish process after completion the set_fen() method.
    
    Stockfish can crash (segfault) on bad input.
    """
    Fault           = 1 # A segmentation fault happened.
    MoverInCheck    = 2 # Stockfish is happy. The GEN position has the mover in check.
    MoverNotInCheck = 3 # Stockfish is happy. The GEN position has the mover *not* in check.


class Stockfish:

    def __init__(self, executable: str):
        self.executable = executable
        self.stockfish = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.close()

    def _send_command(self, command: str) -> None:
        """Send a command to the Stockfish sub-process."""
        if self.stockfish is None:
            raise RuntimeError()
        self.stockfish.stdin.write((command + "\n").encode('ascii'))
        self.stockfish.stdin.flush()

    def _readline(self) -> str:
        """Read a line from the Stockfish sub-process."""
        return self.stockfish.stdout.readline().decode('ascii').strip()

    def open(self) -> None:
        """Start a Stockfish sub-process and put it in "uci" mode."""
        if self.stockfish is not None:
            raise RuntimeError()
        self.stockfish = subprocess.Popen([self.executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self._send_command("uci")
        while True:
            line = self._readline()
            if line == "uciok":
                break

    def close(self) -> None:
        """Tell the Stockfish sub-process to quit, and wait for it to do so."""
        if self.stockfish is None:
            raise RuntimeError()
        self._send_command("quit")
        self.stockfish.wait()

    def wait(self) -> None:
        """Wait for the Stockfish sub-process to terminate."""
        if self.stockfish is None:
            raise RuntimeError()
        self.stockfish.wait()
        self.stockfish = None
    
    def newgame(self) -> None:
        """Send a 'new game' command to Stockfish."""
        if self.stockfish is None:
            raise RuntimeError()
        self._send_command("ucinewgame")

    def ping(self) -> None:
        """Interact with Stockfish and see if we get the expected response."""
        if self.stockfish is None:
            raise RuntimeError()
        self._send_command("isready")
        while True:
            returncode = self.stockfish.poll()
            if returncode is not None:
                raise StockfishException("StockFish quit (exitcode: {})".format(returncode))

            line = self._readline()
            if line == "readyok":
                return

    def set_fen(self, fen: str) -> StockfishStatus:
        """Set a FEN position in the Stockfish sub-process and check its status."""
        self.newgame()
        command = "position fen {}".format(fen)
        self._send_command(command)

        try:
            self.ping()
        except StockfishException:
            # Stockfish crashed.
            # Clean up the sub-process, start a new one, and report failure.
            self.wait()
            self.open()
            return StockfishStatus.Fault

        (readback_fen, in_check) = self._get_fen_and_check_status()
        if fen != readback_fen:
            raise RuntimeError("FEN was not correctly set: {}".format(fen))

        if in_check:
            return StockfishStatus.MoverInCheck
        else:
            return StockfishStatus.MoverNotInCheck

    def _get_fen_and_check_status(self) -> tuple[str, bool]:
        """Check the current status of Stockfish. Get the FEN board, and the in-check status."""
        if self.stockfish is None:
            raise RuntimeError()
        fen = None
        in_check = None
        self._send_command("d")
        while True:
            line = self._readline()
            if line.startswith("Fen: "):
                if fen is not None:
                    raise RuntimeError()
                fen = line[5:]
            elif line.startswith("Checkers:"):
                if in_check is not None:
                    raise RuntimeError()
                in_check = (line != "Checkers:")
                break
        if fen is None:
            raise RuntimeError()
        if in_check is None:
            raise RuntimeError()

        return (fen, in_check)

    def evaluate(self, *, depth: Optional[int]=None, movetime: Optional[int]=None):
        """Get an evaluation from Stockfish of the current position."""
        if self.stockfish is None:
            raise RuntimeError()

        arguments = []

        if depth is not None:
            arguments.extend(["depth", str(depth)])

        if movetime is not None:
            arguments.extend(["movetime", str(movetime)])

        command = "go {}".format(" ".join(arguments))

        self._send_command(command)

        info = None
        while True:
            line = self._readline()
            if line.startswith("info"):
                info = line
            if line.startswith("bestmove"):
                break

        if info is None:
            raise RuntimeError("No info lines found.")

        info_split = info.split()
        idx = info_split.index("score")
        evaluation = " ".join(info_split[idx+1:idx+3])

        return evaluation


def main():

    with contextlib.ExitStack() as exit_stack:

        parser = argparse.ArgumentParser(description="Find interesting positions using theStockfish chess engine.")
    
        parser.add_argument("-m", "--material", default="rnbqkbnrppppppppPPPPPPPPRNBQKBNR", help="material to place randomly on the board")
        parser.add_argument("-e", "--executable", default="stockfish,./stockfish", help="path to the Stockfish executable; may be a comma-separated list (default: stockfish,./stockfish)")
        parser.add_argument("--no-highlight", dest="highlight", action='store_false', help="do not highlight lines with small absolute centipawn value")
        parser.add_argument("--highlight-threshold", type=int, default=20, help="highlight threshold (centipawns)")
        parser.add_argument("--movetime", type=float, help="move time for position evaluation (s)", default=1.0)

        args = parser.parse_args()

        for executable_candidate in args.executable.split(","):
            executable = shutil.which(executable_candidate)
            if executable is not None:
                break
        else:
            print("Please specify path to the Stockfish executable using the --executable command line argument.")
            return

        executable = os.path.abspath(executable)

        if colorama is None:
            args.highlight = False
        else:
            colorama.init()
            exit_stack.callback(colorama.deinit)

        board = Board()

        stockfish = exit_stack.enter_context(Stockfish(executable))

        num_found = 0
        while num_found < 1000:

            board.mk_empty()
            board.place_random_pieces(args.material)

            fen_black = board.fen('b')
            status = stockfish.set_fen(fen_black)
            if status in (StockfishStatus.MoverInCheck, StockfishStatus.Fault):
                continue

            fen_white = board.fen('w')
            status = stockfish.set_fen(fen_white)
            if status in (StockfishStatus.MoverInCheck, StockfishStatus.Fault):
                continue

            t1 = time.monotonic()
            evaluation = stockfish.evaluate(movetime = round(args.movetime * 1000.0))
            t2 = time.monotonic()

            duration = t2 - t1

            if args.highlight:
                highlight_line = evaluation.startswith("cp") and abs(int(evaluation.split()[1])) < args.highlight_threshold            
            else:
                highlight_line = False

            if highlight_line:
                print(colorama.Style.BRIGHT + colorama.Fore.YELLOW, end='')

            num_found += 1
            print("{:6d} evaluation {:20} duration {:10.3f} fen {} ".format(num_found, evaluation, duration, fen_white))

            if highlight_line:
                print(colorama.Style.RESET_ALL, end='')

if __name__ == "__main__":
    main()
