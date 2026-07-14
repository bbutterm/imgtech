from PIL import ImageFilter
import os

basedir = os.path.abspath(os.path.dirname(__file__))

THRESH = 0.93
KERNEL = (15, 2)
ITERATIONS = 2
IM_FILTER = ImageFilter.BLUR
BLEND_LEVEL = 0.4
DPI = 250
IDX1 = 0
IDX2 = 0

class Config(object):
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
                              'sqlite:///' + os.path.join(basedir, 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    INPUTS_DEFAULT = [THRESH, KERNEL, ITERATIONS, IM_FILTER, BLEND_LEVEL, DPI, IDX1, IDX2]
    MAX_CONTENT_LENGTH = 100*1024 * 1024
    UPLOAD_EXTENSIONS = ['.pdf']
    ALLOWED_USERS = ['alext2370@mail.ru', 'grisha1995@gmail.com', 'a.ezaov@grosse-e.ru', 'fadeevaa@inbox.ru', 'test1@mail.ru', 'test2@mail.ru',
                      'test3@mail.ru', 'test44@mail.ru', 'test5@mail.ru',  'test6@mail.ru',]
