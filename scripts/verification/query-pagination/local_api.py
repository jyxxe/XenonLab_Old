import os
from pathlib import Path

import uvicorn

from xeonolab.common import Settings
from xeonolab.library import create_app

settings = Settings(
    data_dir=Path(os.environ['ROUND4_XELIBRARY_DATA']),
    mode='development',
    dev_tokens={
        'round4-local-test-token': {
            'sub': 'round4-synthetic-user',
            'scope': 'xelibrary:read xelibrary:write xelibrary:query',
            'agent_kind': 'human',
        },
    },
    rate_limit_enabled=False,
)
app = create_app(settings)
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ['ROUND4_XELIBRARY_PORT']), log_level='warning')
