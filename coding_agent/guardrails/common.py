import os


def normalise_verification_path(filepath, state):
    if not isinstance(filepath, str) or not filepath.strip():
        return None

    absolute = (
        filepath
        if os.path.isabs(filepath)
        else os.path.join(state.session_cwd, filepath)
    )
    return os.path.normcase(os.path.realpath(absolute))
