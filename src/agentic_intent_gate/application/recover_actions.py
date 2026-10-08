"""Startup only, after ownership + predecessor/helper quiescence, never tool lookup/retry."""


def recover_actions(repository, *, predecessor_stopped=False):
    return repository.recover(owners_stopped=predecessor_stopped)
