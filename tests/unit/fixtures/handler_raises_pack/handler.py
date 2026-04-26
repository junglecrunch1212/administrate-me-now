"""Handler that always raises — exercises the wrapper's handler_raised
failure path."""


def post_process(raw, inputs, ctx):
    raise RuntimeError("intentional handler failure for tests")
