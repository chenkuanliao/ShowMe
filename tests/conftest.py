import os
import tempfile


os.environ.setdefault("SHOWME_DATA_DIR", tempfile.mkdtemp(prefix="showme-tests-"))
os.environ.setdefault("SHOWME_BASE_URL", "http://showme.test")
os.environ.setdefault("SHOWME_SECURE_COOKIES", "false")
