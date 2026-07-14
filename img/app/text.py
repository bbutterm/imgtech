import fitz
import difflib as dl

#функция определения размера страницы
def page_size(path1):
	pdf = fitz.open(path1)
	w = pdf[0].mediabox.width
	h = pdf[0].mediabox.height
	return (w,h)

# функция подсчета количества графических элементов на странице
def drawings_number(path1):
	pdf = fitz.open(path1)
	return len(pdf[0].get_drawings())

#функция сравнения страниц с текстом и поиска координат bbox для разностных слов
def text_comparison(path1, path2):
	#открываем pdf страницы
	doc1 = fitz.open(path1)
	doc2 = fitz.open(path2)
	
	#создаем словари для слов и координат их bbox
	words_dict1 = {'words': [], 'wcoords': []}
	words_dict2 = {'words': [], 'wcoords': []}
	
	words1_list = doc1.get_page_text(0, 'words')
	words2_list = doc2.get_page_text(0, 'words')
		
	#заполняем словари данными
	words_dict1['words'] = [word[4] for word in words1_list]
	words_dict1['wcoords'] = [word[:4] for word in words1_list]
	words_dict2['words'] = [word[4] for word in words2_list]
	words_dict2['wcoords'] = [word[:4] for word in words2_list]
		
	#сравниваем списки слов страниц библиотекой difflib и оставляем только те разницы, которые относятся к странице
	diff_items1 = [diff for diff in dl.ndiff(words_dict1['words'], words_dict2['words']) if not diff.startswith(("+", "?"))]
	diff_items2 = [diff for diff in dl.ndiff(words_dict1['words'], words_dict2['words']) if not diff.startswith(("-", "?"))]
		
	#собираем индексы слов, у которых стоит знак минус или плюс (разностные слова)
	diffs1 = []
	diffs2 = []
	for idx, diff in enumerate(diff_items1):
		if diff.startswith("-"):
			diffs1.append(idx)
		
	for idx, diff in enumerate(diff_items2):
		if diff.startswith("+"):
			diffs2.append(idx)
	#по индексам разностных слов изем координаты их bbox
	bboxes1 = []
	for idx in diffs1:
		bboxes1.append(words_dict1['wcoords'][idx])
		
	bboxes2 = []
	for idx in diffs2:
		bboxes2.append(words_dict2['wcoords'][idx])
		
	return bboxes1, bboxes2, words_dict1, words_dict2
