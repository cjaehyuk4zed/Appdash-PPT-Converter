import multiprocessing
import time


def child_main(connection, task, payload):
    try:
        from .engine import conversion, inspect_file
        if task == "inspect":
            result = inspect_file(payload)
        else:
            result = conversion(payload, lambda event: connection.send(("progress", event)))
        connection.send(("result", result))
    except Exception as error:
        # Do not emit document paths, passwords or native tracebacks to the client/log.
        message = str(error) if isinstance(error, ValueError) else "The file could not be processed. It may be damaged or use an unsupported feature."
        connection.send(("error", message[:800]))
    finally:
        connection.close()


def run_process(task, payload, timeout=600, cancel=None, progress=None):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=child_main, args=(sender, task, payload), daemon=True)
    process.start()
    sender.close()
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Conversion cancelled.")
            if time.monotonic() >= deadline:
                raise TimeoutError("Processing timed out. Try fewer pages or lower resolution.")
            if receiver.poll(0.1):
                try:
                    kind, value = receiver.recv()
                except EOFError:
                    raise RuntimeError("Conversion worker exited unexpectedly.") from None
                if kind == "result":
                    return value
                if kind == "error":
                    raise ValueError(value)
                if progress:
                    progress(value)
            elif not process.is_alive():
                raise RuntimeError("Conversion worker exited unexpectedly.")
    finally:
        receiver.close()
        process.join(timeout=0.25)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=3)
        process.close()
