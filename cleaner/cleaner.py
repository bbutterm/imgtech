import os
import shutil
import time

import schedule

outputs_dir = 'app/static/outputs'

def clean_job():
    try:
        shutil.rmtree(outputs_dir)
        os.mkdir(outputs_dir)
    except OSError:
        pass

schedule.every(600).seconds.do(clean_job)
while True:
    schedule.run_pending()
    time.sleep(5)
