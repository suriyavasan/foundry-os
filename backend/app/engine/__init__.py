"""Work engine processes: scheduler (dispatch, schedules, recovery) and worker (job execution).

Everything here is built around plain ``tick()``/``run_once()`` methods that take an injectable
clock, so tests drive the engine deterministically; the ``*_forever`` loops only add sleeping.
"""
