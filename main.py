# Точка входа для Render: запускает server.py (на случай стартовой команды по умолчанию `python main.py`)
import os, runpy
os.chdir(os.path.dirname(os.path.abspath(__file__)))
runpy.run_path('server.py', run_name='__main__')
