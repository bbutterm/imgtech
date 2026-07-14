from urllib.parse import urlsplit

from app import app, db
from app.forms import UploadForm, SettingForm, LoginForm, RegistrationForm
from app.pictures import partitioning, join_jpeg, compare
from app.pages_aligning import pages_align

from flask_login import current_user, login_user, logout_user, login_required
import sqlalchemy as sa
from app.db_tables import User, Pdfs

from flask import render_template, flash, redirect, url_for, request, send_from_directory, session
from werkzeug.utils import secure_filename

import os
import uuid
import shutil
import time
import numpy as np
from PIL import ImageFilter

inputs_default = app.config['INPUTS_DEFAULT']

@app.route('/home', methods=['GET', 'POST'])
@login_required
def home():
    basedir = os.path.abspath(os.path.dirname(__file__))
    uid1 = str(uuid.uuid4())
    uid2 = str(uuid.uuid4())
    uid3 = str(uuid.uuid4())
    uid4 = str(uuid.uuid4())

    inputs = [os.path.join(basedir, uid1), os.path.join(basedir, uid2),
              os.path.join(basedir, uid3), os.path.join(basedir, uid4)]
    inputs.extend(inputs_default)
    outputs = os.path.join(basedir, 'static/outputs/')

    title = 'Сравнение чертежей и текста'
    dict_out = {'p_nums1': 0, 'p_nums2': 0, 'time': 0,
                'extra_p1': 'нет', 'extra_p2': 'нет', 'res_p': 0}
    page_numbers_list = []
    form = UploadForm()

    if form.validate_on_submit():

        uploaded_files = form.files.data
        for file in uploaded_files:
            filename = secure_filename(file.filename)
            file_ext = os.path.splitext(filename)[1]
            if filename == '':
                flash('Вы не загрузили файлы!')
                return redirect(url_for('home'))
            elif file_ext not in app.config['UPLOAD_EXTENSIONS']:
                error_message_400= "Ошибка 400, загружены один или несколько файлов неверного формата"
                e = {'code': 400}
                return render_template('errors.html', title='что-то пошло не так', error_message_400=error_message_400,
                                       e=e), 400

        if len(form.files.data) != 2:
            flash('Должно быть ровно два документа!')
            return redirect(url_for('home'))
        else:
            os.mkdir(inputs[0])
            os.mkdir(inputs[1])
            os.mkdir(inputs[2])
            os.mkdir(inputs[3])

            pdfs = Pdfs(pdf_names=secure_filename(uploaded_files[0].filename)+"  "+secure_filename(uploaded_files[1].filename),
                        file_compare_author=current_user)
            db.session.add(pdfs)
            db.session.commit()
            
            file1 = form.files.data[0]
            file2 = form.files.data[1]

            file1_path = f'{inputs[0]}/pdf1.pdf'
            file2_path = f'{inputs[1]}/pdf2.pdf'

            file1.save(file1_path)
            file2.save(file2_path)

            start = time.time()
            paths1, paths2 = partitioning(file1_path, file2_path, inputs[0], inputs[1])
            os.remove(file1_path)
            os.remove(file2_path)

            page_nums1 = len(os.listdir(inputs[0]))
            page_nums2 = len(os.listdir(inputs[1]))

            extra_pages1, extra_pages2, page_numbers1_final, page_numbers2_final = pages_align(page_nums1, page_nums2,
                                                                                               paths1, paths2)

            if page_numbers1_final and page_numbers2_final:
                for indices in zip(page_numbers1_final, page_numbers2_final):
                    page_numbers_list.append(inputs[:-2] + list(indices))

                list(map(compare, page_numbers_list))

            fname1 = str(uuid.uuid4()) + '.pdf'
            fname2 = str(uuid.uuid4()) + '.pdf'
            session['path1'] = fname1
            session['path2'] = fname2

            join_jpeg(inputs, outputs + fname1, outputs + fname2)
            end = time.time()
            comparison_time = np.round((end - start), 1)

            dict_out['p_nums1'] = page_nums1
            dict_out['p_nums2'] = page_nums2
            dict_out['time'] = comparison_time
            dict_out['res_p'] = len(os.listdir(inputs[2]))
            dict_out['extra_p1'] = extra_pages1
            dict_out['extra_p2'] = extra_pages2

        shutil.rmtree(inputs[0])
        shutil.rmtree(inputs[1])
        shutil.rmtree(inputs[2])
        shutil.rmtree(inputs[3])

    return render_template('home.html', title=title, form=form, page_numbers_list=page_numbers_list, dict_out=dict_out,
                           data=(inputs[4], inputs[7], inputs[9]))

@app.route('/doc1')
@login_required
def doc1():
    basedir = os.path.abspath(os.path.dirname(__file__))
    outputs = os.path.join(basedir, 'static/outputs/')
    fname1 = session['path1']
    return send_from_directory(outputs, fname1, as_attachment=True, download_name='result1.pdf')

@app.route('/doc2')
@login_required
def doc2():
    basedir = os.path.abspath(os.path.dirname(__file__))
    outputs = os.path.join(basedir, 'static/outputs/')
    fname2 = session['path2']
    return send_from_directory(outputs, fname2, as_attachment=True, download_name='result2.pdf')

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    filter_options = ['EDGE_ENHANCE', 'EDGE_ENHANCE_MORE', 'SMOOTH', 'SMOOTH_MORE', 'SHARPEN', 'DETAIL', 'FIND_EDGES', 'BLUR']

    filter_options_dict = {'EDGE_ENHANCE': ImageFilter.EDGE_ENHANCE, 'EDGE_ENHANCE_MORE': ImageFilter.EDGE_ENHANCE_MORE,
                            'SMOOTH': ImageFilter.SMOOTH, 'SMOOTH_MORE': ImageFilter.SMOOTH_MORE, 'SHARPEN': ImageFilter.SHARPEN,
                            'DETAIL': ImageFilter.DETAIL, 'FIND_EDGES': ImageFilter.FIND_EDGES, 'BLUR': ImageFilter.BLUR}
    filter_options_dict_swapped = dict((v, k) for k, v in filter_options_dict.items())

    form = SettingForm()
    form.select_filter.choices = filter_options
    form.select_thresh.data = inputs_default[0]
    form.select_kernel_w.data = inputs_default[1][0]
    form.select_kernel_h.data = inputs_default[1][1]
    form.select_iterations.data = inputs_default[2]
    form.select_filter.data = filter_options_dict_swapped[inputs_default[3]]
    form.select_blend.data = inputs_default[4]
    form.select_dpi.data = inputs_default[5]

    if form.validate_on_submit():

        inputs_default[0] = float(request.form['select_thresh'])
        inputs_default[1] = (int(request.form['select_kernel_w']), int(request.form['select_kernel_h']))
        inputs_default[2] = int(request.form['select_iterations'])
        inputs_default[3] = filter_options_dict[request.form['select_filter']]
        inputs_default[4] = float(request.form['select_blend'])
        inputs_default[5] = int(request.form['select_dpi'])

        form.select_thresh.data = inputs_default[0]
        form.select_kernel_w.data = inputs_default[1][0]
        form.select_kernel_h.data = inputs_default[1][1]
        form.select_iterations.data = inputs_default[2]
        form.select_filter.data = request.form['select_filter']
        form.select_blend.data = inputs_default[4]
        form.select_dpi.data = inputs_default[5]

    return render_template('settings.html', title='Параметры', form=form, data=inputs_default)

@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(sa.select(User).where(User.username == form.username.data))
        if user is None or not user.check_password(form.password.data):
            flash('Неверное имя пользователя или пароль!')
            return redirect(url_for('login'))
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or urlsplit(next_page).netloc != '':
            next_page = url_for('home')
        return redirect(next_page)
    return render_template('login.html', title='Авторизация', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = RegistrationForm()
    if form.validate_on_submit():
        if form.email.data in app.config['ALLOWED_USERS']:
            user = User(username=form.username.data, email=form.email.data, city=form.city.data,
                        company=form.company.data, profession=form.profession.data)
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash('Регистрация прошла успешно!')
            return redirect(url_for('login'))
        else:
            flash('Вы не авторизованный для регистрации пользователь!')
            return redirect(url_for('login'))
    return render_template('register.html', title='Регистрация', form=form)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.errorhandler(413)
def too_large(e):
    error_message_413 = "Ошибка 413, один или несколько файлов превысили лимит в 10МБ"
    return render_template('errors.html', title='что-то пошло не так', error_message_413 = error_message_413, e=e), 413

@app.errorhandler(404)
def not_found_error(e):
    error_message_404 = "Ошибка 404, страница или данные не найдены"
    return render_template('errors.html', title='что-то пошло не так', error_message_404 = error_message_404, e=e), 404

@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    error_message_500 = "Ошибка 500, сервер не может обработать ваш запрос, обратитесь к администратору"
    return render_template('errors.html', title='что-то пошло не так', error_message_500 = error_message_500, e=e), 500
