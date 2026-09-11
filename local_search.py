"""Small in-process BM25 inverted index over saved metadata; zero model calls."""
from collections import Counter, defaultdict
import math
import re
import unicodedata

FIELD_SPECS = [('title', '题目', 3, 600), ('keywords', '关键词', 3, 1200),
    ('description', '个人描述', 2, 4000), ('abstract', '摘要', 1, 6000),
    ('conclusion', '结论', 1, 6000), ('limitations', '局限', 1, 3000),
    ('summary', '总结', 1, 4000), ('major', '大类', 1, 100), ('minor', '小类', 1, 100)]
ALIASES = [
    ('大语言模型', '大模型', 'large language model', 'llm'),
    ('检索增强生成', 'retrieval augmented generation', 'rag'),
    ('机器学习', 'machine learning'), ('深度学习', 'deep learning'),
    ('图像分割', 'image segmentation'), ('目标检测', 'object detection'),
    ('能耗', '能源消耗', 'energy consumption', 'power consumption'),
    ('预测', 'forecast', 'forecasting', 'prediction', 'predictive'),
    ('局限', '局限性', '不足', 'limitations', 'limitation'),
    ('分类', '归类', 'classification', 'classify'),
    ('电池', 'battery', 'batteries'), ('储能', 'energy storage'),
    ('时序', '时间序列', 'time series'), ('知识图谱', 'knowledge graph'),
    ('泛化', '泛化能力', 'generalization', 'generalisation'),
    ('鲁棒性', 'robustness'), ('过拟合', 'overfitting'),
    ('结论', 'conclusion', 'conclusions'),
]
EN_STOP = set('a an the of to for and or in on with by is are was were be from this that paper papers study studies research find about using use i want which can could have has it its'.split())
CN_STOP = re.compile(r'有没有|是否有|帮我|我想|请问|查找|找一下|相关的|相关|关于|研究|论文|文献|能够|可以|一个|一些|这种|方法|进行|以及|如何|需要')


def tokens(text):
    text = unicodedata.normalize('NFKC', str(text)).casefold()
    result = []
    for i, group in enumerate(ALIASES):
        if any(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text) if term.isascii() else term in text for term in group):
            result.append(f'alias{i}')
    for word in re.findall(r'[a-z][a-z0-9_-]*|[\u4e00-\u9fff]+', CN_STOP.sub(' ', text)):
        if word.isascii():
            if word not in EN_STOP:
                result.append(word[:-1] if len(word) > 4 and word.endswith('s') else word)
        else:
            result.extend(word[i:i + 2] for i in range(len(word) - 1))
    return result


class BM25Index:
    def __init__(self):
        self.signature = None
        self.rows = {}
        self.postings = {}
        self.lengths = {}
        self.average = 1

    def update(self, rows):
        signature = tuple((p['id'], *(p.get(key, '') for key, _, _, _ in FIELD_SPECS)) for p in rows)
        # Keep non-indexed paths/links current even if the terms did not change.
        self.rows = {p['id']: p for p in rows}
        if signature == self.signature:
            return
        self.signature = signature
        self.postings = defaultdict(dict)
        self.lengths = {}
        for paper in rows:
            counts = Counter()
            for key, caption, weight, limit in FIELD_SPECS:
                text = paper.get(key, '')[:limit]
                field_tokens = Counter(tokens(text + (' ' + caption if text and key in ('conclusion', 'limitations') else '')))
                for term, count in field_tokens.items():
                    counts[term] += count * weight
            self.lengths[paper['id']] = sum(counts.values())
            for term, count in counts.items():
                self.postings[term][paper['id']] = count
        self.average = max(1, sum(self.lengths.values()) / max(1, len(rows)))

    def search(self, query, limit=10):
        terms = set(tokens(query))
        if not terms or not self.rows:
            return []
        scores = defaultdict(float)
        total = len(self.rows)
        for term in terms:
            posting = self.postings.get(term, {})
            idf = math.log(1 + (total - len(posting) + 0.5) / (len(posting) + 0.5))
            for paper_id, count in posting.items():
                denominator = count + 1.5 * (0.25 + 0.75 * self.lengths[paper_id] / self.average)
                scores[paper_id] += idf * count * 2.5 / denominator
        results = []
        for paper_id, score in sorted(scores.items(), key=lambda entry: (-entry[1], -entry[0]))[:limit]:
            paper = self.rows[paper_id]
            evidence = []
            for key, caption, _, length in FIELD_SPECS:
                text = paper.get(key, '')[:length]
                matched = terms.intersection(tokens(text + (' ' + caption if text and key in ('conclusion', 'limitations') else '')))
                if matched:
                    # Choose a matching sentence, rather than inventing a description.
                    pieces = re.split(r'(?<=[。！？.!?])\s*|\n+', text)
                    best = max(pieces, key=lambda s: len(terms.intersection(tokens(s))))
                    evidence.append({'field': caption, 'text': best[:450], 'hits': len(matched)})
            evidence.sort(key=lambda e: e['hits'], reverse=True)
            results.append({'paper': paper, 'score': round(score, 3), 'evidence': evidence[:3]})
        return results
