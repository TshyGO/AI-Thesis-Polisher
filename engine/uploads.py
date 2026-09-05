"""Pure upload identity check, independent of Streamlit/Word."""
import hashlib
from pathlib import Path


def upload_identity(data, name, state):
    digest = hashlib.sha256(data).hexdigest()
    changed = (state.get("last_uploaded_hash") != digest
               or state.get("last_uploaded_name") != name
               or not Path(state.get("work_copy_path", "__missing__")).is_file())
    return digest, changed
