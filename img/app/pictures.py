import numpy as np
import fitz

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageEnhance

Image.MAX_IMAGE_PIXELS = 1000000000

import os
import glob
import torch
import cv2

from app.text import text_comparison, page_size, drawings_number
from app.model_ae import transform_encoder, model_encoder

model_encoder.load_state_dict(torch.load('./app/models/model_ae_v3_100.pth', map_location=torch.device('cpu')))
cv2.setUseOptimized(True)


# функция разделения pdf файлов на отдельные страницы для послеующего их сравнения
def partitioning(file1_path, file2_path, DIRNAME1, DIRNAME2):
    paths1 = []
    paths2 = []
    pdf1 = fitz.open(file1_path)

    for idx, _ in enumerate(pdf1):
        pdf_page = fitz.open()
        pdf_page.insert_pdf(pdf1, from_page=idx, to_page=idx)
        pdf_page.save(f'{DIRNAME1}/pdf1_{idx}.pdf')
        pdf_page.close()
        paths1.append(f'{DIRNAME1}/pdf1_{idx}.pdf')

    pdf2 = fitz.open(file2_path)

    for idx, _ in enumerate(pdf2):
        pdf_page = fitz.open()
        pdf_page.insert_pdf(pdf2, from_page=idx, to_page=idx)
        pdf_page.save(f'{DIRNAME2}/pdf2_{idx}.pdf')
        pdf_page.close()
        paths2.append(f'{DIRNAME2}/pdf2_{idx}.pdf')
    return paths1, paths2


# функция конвертации jpeg файла в pdf файл
def jpg_to_pdf(filepath, doc):
    img = fitz.open(filepath)  # open pic as document
    rect = img[0].rect  # pic dimension
    pdfbytes = img.convert_to_pdf()  # make a PDF stream
    img.close()  # no longer needed
    imgPDF = fitz.open("pdf", pdfbytes)  # open stream as PDF
    page = doc.new_page(width=rect.width, height=rect.height)
    page.show_pdf_page(rect, imgPDF, 0)

    return doc


# функция поиска областей картинки с разным содержанием
def find_nonsimilarities(im1, im2, area_lower_limit=10, area_upper_limit=1000,
                         threshold=0, kernel=(2, 2), iterations=1,
                         transform=None, model=None, distance_thresh=0.9,
                         im_filter1=ImageFilter.EDGE_ENHANCE, im_filter2=ImageFilter.BLUR):
    # конвертируем картинки в серые картинки
    g1 = im1.convert('L')
    g2 = im2.convert('L')
    # ищем попиксельную разность
    difference = ImageChops.difference(g1, g2)

    # конвертируем разностную картинку черно белую, где белым выделены области с разностями
    f1 = np.array(difference.filter(im_filter1))
    f2 = np.array(difference.filter(im_filter2))
    im = cv2.add(f1, f2)

    ret, thresh = cv2.threshold(im, threshold, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # размываем области в разными пикселями для формирования сплошных областей под обводку контурами
    rect_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, kernel)
    dilation = cv2.dilate(thresh, rect_kernel, iterations=iterations)

    # находим контуры разностных областей
    contours, hierarchy = cv2.findContours(dilation, cv2.RETR_CCOMP,
                                           cv2.CHAIN_APPROX_NONE)

    # создадим новые пустые списки для заполнения их контурами, прошедшими через фильтры
    # размера площади и уровня иерархии по индексу наличия родителя
    cont_filtered = []
    for idx, cnt in enumerate(contours):
        x, y, w, h = cv2.boundingRect(cnt)
        if cv2.contourArea(cnt) > area_lower_limit and cv2.contourArea(cnt) < area_upper_limit and \
                hierarchy[:, idx, -1][0] == -1:
            cont_filtered.append(cnt)

    # создаем список с координатами боксов, внутри которых разностные области
    coords = []
    for cnt in cont_filtered:
        coords.append(cv2.boundingRect(cnt))

    coords_new = []
    # прогоняем области внутри боксов через энкодер и сравниваем вектора картинок в пространстве признаков
    # сильно близкие вектора отсеиваем, считая их одинаковыми картинками в боксах
    for coord in coords:

        x, y, w, h = coord
        diff1 = g1.crop((x, y, x + w, y + h))
        diff2 = g2.crop((x, y, x + w, y + h))

        diff1_contrast = ImageEnhance.Contrast(diff1).enhance(3)
        diff2_contrast = ImageEnhance.Contrast(diff2).enhance(3)
        diff1_sharp = ImageEnhance.Sharpness(diff1_contrast).enhance(3)
        diff2_sharp = ImageEnhance.Sharpness(diff2_contrast).enhance(3)

        diff1_transformed = transform_encoder(diff1_sharp)
        diff2_transformed = transform_encoder(diff2_sharp)

        with torch.no_grad():
            emb1 = model.encoder(diff1_transformed.unsqueeze(0))
            emb2 = model.encoder(diff2_transformed.unsqueeze(0))
            distance = np.round(torch.nn.functional.cosine_similarity(emb1, emb2, dim=1, eps=1e-8).item(), 2)

        if distance < distance_thresh:
            coords_new.append(coord)

    return coords_new


# функция сравнения картинок и сохранения картинки с выделенными в боксы разностными областями
def get_pictures_blend(im1_1, im1_2, im2_1, im2_2, bboxes1, bboxes2, page_size1, page_size2, draws, page_number1,
                       page_number2,
                       distance_thresh, kernel, iterations, im_filter1, im_filter2, blend_level, path1, path2):
    pic_size1 = im1_1.size
    pic_size2 = im1_2.size

    ratio_x1 = pic_size1[0] / page_size1[0]
    ratio_y1 = pic_size1[1] / page_size1[1]

    ratio_x2 = pic_size2[0] / page_size2[0]
    ratio_y2 = pic_size2[1] / page_size2[1]

    bboxes_extended1 = []
    if bboxes1:
        for box in bboxes1:
            x0, y0, x1, y1 = box
            bboxes_extended1.append((x0 * ratio_x1, y0 * ratio_y1, (x1 - x0) * ratio_x1, (y1 - y0) * ratio_y1))

    bboxes_extended2 = []
    if bboxes2:
        for box in bboxes2:
            x0, y0, x1, y1 = box
            bboxes_extended2.append((x0 * ratio_x2, y0 * ratio_y2, (x1 - x0) * ratio_x2, (y1 - y0) * ratio_y2))

    # Font selection from the downloaded file
    font_size = pic_size1[0] // 40
    myFont2 = ImageFont.truetype('./app/fonts/isocpeur.ttf', font_size)

    if draws < 400 or pic_size1 != pic_size2:
        coords1 = bboxes_extended1
        coords2 = bboxes_extended2
    else:
        bboxes_images = find_nonsimilarities(im2_1, im2_2, area_lower_limit=400, area_upper_limit=float('inf'),
                                             threshold=200, kernel=kernel, iterations=iterations,
                                             transform=transform_encoder, model=model_encoder,
                                             distance_thresh=distance_thresh,
                                             im_filter1=im_filter1, im_filter2=im_filter2)

        coords1 = bboxes_images + bboxes_extended1
        coords2 = bboxes_images + bboxes_extended2

    if len(coords1) > 0 or len(coords2) > 0:
        background1 = Image.new("RGB", pic_size1, (255, 255, 255))
        background1_draw = ImageDraw.Draw(background1)

        background2 = Image.new("RGB", pic_size2, (255, 255, 255))
        background2_draw = ImageDraw.Draw(background2)

        for coord_idx1, coord1 in enumerate(coords1):
            x, y, w, h = coord1
            background1_draw.rectangle([(x, y), (x + w, y + h)], fill=None, outline="red", width=4)

        for coord_idx2, coord2 in enumerate(coords2):
            x, y, w, h = coord2
            background2_draw.rectangle([(x, y), (x + w, y + h)], fill=None, outline="red", width=4)

        background1_draw.text((10, 50),
                              text=f'Страница номер: {page_number1 + 1}, сравнивалась со страницей {page_number2 + 1} во втором документе',
                              fill=(255, 0, 0), font=myFont2)
        background2_draw.text((10, 50),
                              text=f'Страница номер: {page_number2 + 1}, сравнивалась со страницей {page_number1 + 1} в первом документе',
                              fill=(255, 0, 0), font=myFont2)
        result1 = Image.blend(background1, im1_1, blend_level)
        result2 = Image.blend(background2, im1_2, blend_level)

        result1.save(path1, optimize=True, quality=15)
        result2.save(path2, optimize=True, quality=15)


def compare(inputs_list):
    path1 = f'{inputs_list[0]}/pdf1_{inputs_list[10]}.pdf'
    path2 = f'{inputs_list[1]}/pdf2_{inputs_list[11]}.pdf'
    path1_jpg = f'{inputs_list[2]}/{inputs_list[10]}.jpeg'
    path2_jpg = f'{inputs_list[3]}/{inputs_list[11]}.jpeg'

    draws = min(drawings_number(path1), drawings_number(path2))

    page_size1 = page_size(path1)
    page_size2 = page_size(path2)
    page_number1 = inputs_list[10]
    page_number2 = inputs_list[11]

    b1, b2, w1, w2 = text_comparison(path1, path2)

    doc1_1 = fitz.open(path1)
    doc1_2 = fitz.open(path2)
    doc2_1 = fitz.open(path1)
    doc2_2 = fitz.open(path2)

    rot1 = doc1_1[0].rotation
    rot2 = doc1_2[0].rotation

    if rot1 != 0:
       doc1_1[0].set_rotation(0)
    if rot2 != 0:
       doc1_2[0].set_rotation(0)
    if draws >= 500:
        for box in w1['wcoords']:
            doc2_1[0].add_redact_annot(box, '', cross_out=False)
            doc2_1[0].apply_redactions(images=0, graphics=0, text=0)

        for box in w2['wcoords']:
            doc2_2[0].add_redact_annot(box, '', cross_out=False)
            doc2_2[0].apply_redactions(images=0, graphics=0, text=0)

    pix1_1 = doc1_1[0].get_pixmap(dpi=inputs_list[9])
    im1_1 = Image.frombytes('RGB', [pix1_1.width, pix1_1.height], pix1_1.samples)

    pix1_2 = doc1_2[0].get_pixmap(dpi=inputs_list[9])
    im1_2 = Image.frombytes('RGB', [pix1_2.width, pix1_2.height], pix1_2.samples)

    pix2_1 = doc2_1[0].get_pixmap(dpi=inputs_list[9])
    im2_1 = Image.frombytes('RGB', [pix2_1.width, pix2_1.height], pix2_1.samples)

    pix2_2 = doc2_2[0].get_pixmap(dpi=inputs_list[9])
    im2_2 = Image.frombytes('RGB', [pix2_2.width, pix2_2.height], pix2_2.samples)

    get_pictures_blend(im1_1, im1_2, im2_1, im2_2, b1, b2, page_size1, page_size2, draws, page_number1, page_number2,
                       inputs_list[4], inputs_list[5], inputs_list[6], ImageFilter.EDGE_ENHANCE, inputs_list[7],
                       inputs_list[8], path1_jpg, path2_jpg)


def join_jpeg(inputs_list, path1, path2):
    doc1 = fitz.open()
    doc2 = fitz.open()
    if os.listdir(inputs_list[2]) and os.listdir(inputs_list[3]):
        list_of_jpeg1 = sorted(glob.glob(f'{inputs_list[2]}/*.jpeg'), key=lambda x: int(x[len(inputs_list[2]) + 1:-5]))
        list_of_jpeg2 = sorted(glob.glob(f'{inputs_list[3]}/*.jpeg'), key=lambda x: int(x[len(inputs_list[3]) + 1:-5]))
        for f1 in list_of_jpeg1:
            doc1 = jpg_to_pdf(f1, doc1)
        for f2 in list_of_jpeg2:
            doc2 = jpg_to_pdf(f2, doc2)

        doc1.save(path1)
        doc2.save(path2)
