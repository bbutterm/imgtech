import fitz
import difflib as dl
import os
import numpy as np
import multiprocessing as mp
from skimage.metrics import structural_similarity

import PIL
from PIL import Image

from app.text import drawings_number

#функция измерения расстояния между текстами страниц
def pages_distance(pdf_file_path):
	#константа для избежания операции деления на ноль при вычислении доли слов в тексте страницы со знаком минус
	eps = 0.001

	draws1 = drawings_number(pdf_file_path[0])
	draws2 = drawings_number(pdf_file_path[1])
	draws = max(draws1, draws2)

	#создаем два pdf документа для работы
	doc1 = fitz.open(pdf_file_path[0])
	doc2 = fitz.open(pdf_file_path[1])

	#если в странице 1 и 2 есть текст, то вытаскиваем все слова в списки и сравниваем списки библиотекой difflib
	if doc1.get_page_text(0,'lines') and doc2.get_page_text(0,'lines') and draws < 500:
		words1 = doc1.get_page_text(0,'words')
		words2 = doc2.get_page_text(0,'words')
	
		words_list1 = [word[4] for word in words1]
		words_list2 = [word[4] for word in words2]
	
		#подсчитываем долю слов в первой странице, не встречающихся во второй странице
		diff_counts = 0
		diffs = dl.ndiff(words_list1, words_list2)
		for diff in diffs:
			if not diff.startswith(('-', '+', '?')):
				diff_counts += 1
		dist = diff_counts/(max(len(words_list1), len(words_list2))+eps)
	#а если в обоих документах отсутствует текст (там картинки), то сравниваем картинки
	elif not doc1.get_page_text(0,'lines') and not doc2.get_page_text(0,'lines'):
		pix1 = doc1[0].get_pixmap()
		pix2 = doc2[0].get_pixmap()
		if pix1.samples == pix2.samples:
			dist = 1.0
		else:
			dist = 0.0
	# если в одном из документов превалирует графика, то преобразуем в картинки и сравниваем как картинки
	elif draws >= 500:

		pix1 = doc1[0].get_pixmap()
		im1 = Image.frombytes('L', [pix1.width, pix1.height], pix1.samples)

		pix2 = doc2[0].get_pixmap()
		im2 = Image.frombytes('L', [pix2.width, pix2.height], pix2.samples)

		im2 = im2.resize(im1.size)

		im1_np = np.asarray(im1)
		im2_np = np.asarray(im2)
		score, _ = structural_similarity(im1_np, im2_np, full=True)

		if score >= 0.2:
			dist = score
		else:
			dist = 0
	else:
		dist = 0.0

	return dist
	

	#elif draws >= 500:
		#rot1 = doc1[0].rotation
		#rot2 = doc2[0].rotation

		#if rot1 != 0:
			#doc1[0].set_rotation(0)
		#else:
			#pass
		#if rot2 != 0:
			#doc2[0].set_rotation(0)
		#else:
			#pass
    

		#pix1 = doc1[0].get_pixmap()
		#im1 = Image.frombytes('L', [pix1.width, pix1.height], pix1.samples)
		#im1_np = np.asarray(im1)

		#pix2 = doc2[0].get_pixmap()
		#im2 = Image.frombytes('L', [pix2.width, pix2.height], pix2.samples)
		#im2_np = np.asarray(im2)

		#if im1_np.shape != im2_np.shape:
			#dist = 0.0
		#else:
			#score, _ = structural_similarity(im1_np, im2_np, full=True)
			#if score > 0.7:
				#dist = score
			#else:
				#dist = 0
	#если в одной картинке есть текст, а вдругой нет, то расстояние 0, то есть они полностью разные
	#else:
		#dist = 0.0
	
	#return dist

#функция поиска лишних страниц в сравниваемых документах и их удаление из списков путей к страницам
def pages_align(page_nums1, page_nums2, paths1, paths2):

	#создаем изначальный список номеров страниц
	page_numbers1_initial = list(np.linspace(0, page_nums1-1, page_nums1, dtype=int))
	page_numbers2_initial = list(np.linspace(0, page_nums2-1, page_nums2, dtype=int))
	#создаем конечный список номеров страниц
	page_numbers1_final = []
	page_numbers2_final = []

	#создаем списк пар сравниваемых по расстоянию страниц и соответствующих этим парам пар путей к файлам
	pages_pairs = []
	paths_pairs = []

	for page1 in page_numbers1_initial:
		for page2 in range(max(0, page1-5), min(page1+5, len(page_numbers2_initial))):
			paths_pairs.append((paths1[page1],paths2[page2]))
			pages_pairs.append((page1, page2))
	
	#создаем список расстояний между парами страниц
	#pages_dists = [dist for dist in map(pages_distance, paths_pairs)]
	with mp.Pool() as p:
		pages_dists = p.map(pages_distance, paths_pairs)

	#проходим по сортированному списку пар страниц и расстояний между ними от наименьшего расстояни к наибольшему
	#удаляем не прошедшие фильтр пары из первоаначального списка
	#в финальный список наоборот добавляем прошедшие фильтр пары
	for dist in sorted(zip(pages_dists, pages_pairs), reverse=True):
	#for dist in sorted(dists_pages_pairs_filtered, reverse=True):
		if dist[1][0] in page_numbers1_initial and dist[1][1] in page_numbers2_initial and dist[0] > 0.1:
			page_numbers1_final.append(dist[1][0])
			page_numbers2_final.append(dist[1][1])
			page_numbers1_initial.remove(dist[1][0])
			page_numbers2_initial.remove(dist[1][1])

	if page_nums1 != page_nums2:
		if page_numbers1_initial:
			extra_pages1 = ', '.join(str(x+1) for x in page_numbers1_initial)
		else:
			extra_pages1 = 'нет'
		if page_numbers2_initial:
			extra_pages2 = ', '.join(str(x+1) for x in page_numbers2_initial)
		else:
			extra_pages2 = 'нет'
	else:
		extra_pages1 = 'нет'
		extra_pages2 = 'нет'

	return extra_pages1, extra_pages2, page_numbers1_final, page_numbers2_final
