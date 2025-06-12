import sys
import time
import random
import argparse
import os # For clearing the screen

def stream_text(text_content, speed_cps=40, randomness_factor=0.3):
    """
    Streams the given text_content character by character to the console.

    Args:
        text_content (str): The string content to stream.
        speed_cps (float): Average characters per second.
        randomness_factor (float): A factor for speed variation (0.0 to 1.0).
                                   e.g., 0.3 means +/- 30% variation.
    """
    if not text_content: # Handle empty content
        print() # Still print a newline to finish the "stream"
        return

    base_delay = 1.0 / speed_cps
    min_delay = base_delay * (1 - randomness_factor)
    max_delay = base_delay * (1 + randomness_factor)

    # Ensure min_delay is not excessively small or negative, and at least 1ms
    min_delay = max(0.001, min_delay)

    for char in text_content:
        print(char, end='', flush=True)
        # Introduce a slight random delay
        delay = random.uniform(min_delay, max_delay)
        time.sleep(delay)
    print() # Add a newline after the whole content is streamed for this iteration

def main():
    parser = argparse.ArgumentParser(
        description="Streams a file's content character by character to the console in a loop."
    )
    parser.add_argument(
        "filepath",
        type=str,
        help="Relative or absolute path to the file to stream."
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=40.0,
        help="Average characters per second (default: 40.0)."
    )
    parser.add_argument(
        "--randomness",
        type=float,
        default=0.3,
        help="Speed randomness factor (0.0 to 1.0, default: 0.3 for +/-30%%)."
    )
    parser.add_argument(
        "--loop-delay",
        type=float,
        default=2.0,
        help="Delay in seconds between streaming loops (default: 2.0)."
    )
    parser.add_argument(
        "--clear",
        action="store_true", # Makes it a boolean flag
        help="If set, clears the screen before each streaming iteration."
    )

    args = parser.parse_args()

    # Validate arguments
    if not (0.0 <= args.randomness <= 1.0):
        print("Error: Randomness factor must be between 0.0 and 1.0.")
        sys.exit(1)
    if args.speed <= 0:
        print("Error: Speed must be a positive value.")
        sys.exit(1)
    if args.loop_delay < 0:
        print("Error: Loop delay must be non-negative.")
        sys.exit(1)

    # Read file content once
    try:
        with open(args.filepath, 'r', encoding='utf-8') as f:
            content_to_stream = f.read()
    except FileNotFoundError:
        print(f"Error: File not found at '{args.filepath}'")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file '{args.filepath}': {e}")
        sys.exit(1)

    if not content_to_stream.strip():
        print(f"Warning: The file '{args.filepath}' is empty or contains only whitespace.")
        # The script will still loop, streaming "nothing" with pauses.

    loop_count = 0
    try:
        while True:
            loop_count += 1
            if args.clear:
                # Clear console: 'cls' for Windows, 'clear' for Linux/macOS
                os.system('cls' if os.name == 'nt' else 'clear')

            # Optional: Print a header for each iteration
            header_message = f"--- Streaming '{os.path.basename(args.filepath)}' (Iteration {loop_count}, Ctrl+C to stop) ---"
            print(header_message)

            stream_text(content_to_stream, args.speed, args.randomness)

            # Optional: Print a footer message or just pause
            if args.clear:
                print(f"--- Finished. Waiting {args.loop_delay}s for next loop ---")
            else:
                # If not clearing, just add an extra newline for visual separation
                # The stream_text function already adds one.
                print()

            time.sleep(args.loop_delay)

    except KeyboardInterrupt:
        print("\n\nStreaming interrupted by user.") # Add extra newlines for clarity after ^C
    finally:
        print("Script finished.")

if __name__ == "__main__":
    main()