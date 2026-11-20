import os,json
from pathlib import Path
_cfg=Path(os.environ.get('PROJECT_JSON','/app/project.json'))
if not _cfg.exists():
 _cfg=Path(__file__).resolve().parents[2]/'project.json'
CONFIG=json.loads(_cfg.read_text())
DATABASE_URL=os.environ['DATABASE_URL']
REDIS_URL=os.getenv('REDIS_URL','redis://redis:6379/0')
SECRET=os.environ['JWT_SECRET']
