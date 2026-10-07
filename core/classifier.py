"""
Document classification module for ASTREX v3.

Provides text classification, language detection, sentiment analysis,
and topic extraction capabilities.
"""

import re
from typing import List, Dict, Tuple, Set
from dataclasses import dataclass
from collections import Counter, defaultdict

from .logging_setup import get_logger

logger = get_logger("astrex.classifier")


# Simple Russian stopwords set
RUSSIAN_STOPWORDS: Set[str] = {
    "и", "в", "на", "с", "по", "не", "что", "это", "как", "для",
    "из", "за", "к", "о", "от", "до", "или", "но", "а", "у",
    "при", "так", "же", "бы", "был", "была", "было", "были",
    "быть", "есть", "будет", "более", "эти", "этот",
    "весь", "вся", "все", "который", "которая", "которые", "которых",
    "также", "является", "может", "можно", "этого", "этой", "только",
    "через", "после", "когда", "между", "своей", "своего", "очень"
}


@dataclass
class ClassificationResult:
    """Result of document classification."""
    category: str
    confidence: float
    language: str
    sentiment: str  # positive, negative, neutral
    topics: List[str]
    word_count: int
    char_count: int


class DocumentClassifier:
    """
    Document classifier with Russian keyword-based categorization,
    language detection, sentiment analysis, and topic extraction.
    """

    def __init__(self):
        """Initialize classifier with predefined categories and keywords."""
        self.logger = get_logger("astrex.classifier")

        # Predefined categories: ключевые слова сравниваются с НАЧАЛОМ слова
        # (префикс), фразы — целиком; ё приводится к е.
        self.categories = {
            "финансы": [
                "оплат", "счет", "баланс", "перевод", "сумм", "рубл",
                "доллар", "банк", "бюджет", "прибыл", "убыт", "налог", "кредит"
            ],
            "юридический": [
                "договор", "контракт", "иск", "суд", "закон", "прав",
                "устав", "лицензи", "протокол", "кодекс", "истец", "ответчик"
            ],
            "кадры": [
                "сотрудник", "зарплат", "увольн", "прием на работу", "отпуск",
                "больничн", "трудов", "ваканси", "резюме", "штатн"
            ],
            "техническая": [
                "сервер", "исходный код", "программн", "api", "база данных", "deploy",
                "git", "docker", "скрипт", "репозитор", "kubernetes", "python"
            ],
            "переписка": [
                "уважаем", "здравствуйте", "с уважением", "добрый день",
                "письм", "ответ на"
            ],
            "отчёт": [
                "отчет", "итог", "результат", "анализ", "показател",
                "динамик", "квартал"
            ]
        }
        self.categories = {
            cat: [self._norm(k) for k in kws] for cat, kws in self.categories.items()
        }

        # Sentiment keywords (префиксы слов)
        self.positive_words = {
            "успех", "хорош", "отличн", "прибыль", "рост", "одобрен",
            "достижен", "улучшен", "эффективн", "качеств"
        }

        self.negative_words = {
            "убыт", "штраф", "нарушен", "отказ", "проблем", "ошибк",
            "жалоб", "недостат", "плох", "снижен"
        }

    def classify(self, text: str) -> ClassificationResult:
        """
        Classify document into predefined categories.

        Args:
            text: Text to classify

        Returns:
            ClassificationResult with classification details
        """
        if not text or not text.strip():
            self.logger.warning("Empty text provided for classification")
            return ClassificationResult(
                category="общий",
                confidence=0.0,
                language="unknown",
                sentiment="neutral",
                topics=[],
                word_count=0,
                char_count=0
            )

        text_lower = self._norm(text)
        words = re.findall(r'\w+', text_lower)
        total_words = len(words)

        # Calculate scores for each category
        scores = {}
        for category, keywords in self.categories.items():
            count = 0
            for keyword in keywords:
                if ' ' in keyword:
                    # Фраза: ищем целиком по границам слов
                    count += len(re.findall(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', text_lower))
                else:
                    # Слово: совпадение по началу слова (а не по подстроке —
                    # иначе "иск" находился в "риск", "код" в "кодекс")
                    count += sum(1 for word in words if word.startswith(keyword))

            # Normalize by total words
            scores[category] = count / total_words if total_words > 0 else 0.0

        # Get top two scores
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        if not sorted_scores or sorted_scores[0][1] == 0.0:
            category = "общий"
            confidence = 0.5
        else:
            category = sorted_scores[0][0]
            best_score = sorted_scores[0][1]

            # Calculate confidence
            if len(sorted_scores) > 1 and sorted_scores[1][1] > 0:
                second_score = sorted_scores[1][1]
                confidence = best_score / (best_score + second_score)
            else:
                # Only one category matched — that's unambiguous, signal high confidence
                confidence = 0.9

        # Detect other properties
        language = self.detect_language(text)
        sentiment = self.detect_sentiment(text_lower)
        topics = self.extract_topics(text.lower())

        self.logger.debug(
            f"Classified text: category={category}, confidence={confidence:.2f}, "
            f"language={language}, sentiment={sentiment}"
        )

        return ClassificationResult(
            category=category,
            confidence=confidence,
            language=language,
            sentiment=sentiment,
            topics=topics,
            word_count=total_words,
            char_count=len(text)
        )

    @staticmethod
    def _norm(text: str) -> str:
        """Нижний регистр и ё → е."""
        return (text or '').lower().replace('ё', 'е')

    def detect_language(self, text: str) -> str:
        """
        Detect language based on character distribution.

        Args:
            text: Text to analyze

        Returns:
            Language code: "ru", "en", or "mixed"
        """
        if not text:
            return "unknown"

        cyrillic_count = len(re.findall(r'[а-яА-ЯёЁ]', text))
        latin_count = len(re.findall(r'[a-zA-Z]', text))
        total = cyrillic_count + latin_count

        if total == 0:
            return "unknown"

        cyrillic_ratio = cyrillic_count / total
        latin_ratio = latin_count / total

        if cyrillic_ratio > 0.7:
            return "ru"
        elif latin_ratio > 0.7:
            return "en"
        else:
            return "mixed"

    def detect_sentiment(self, text: str) -> str:
        """
        Detect sentiment based on keyword matching.

        Args:
            text: Text to analyze (should be lowercased)

        Returns:
            Sentiment: "positive", "negative", or "neutral"
        """
        if not text:
            return "neutral"

        words = re.findall(r'\w+', self._norm(text))  # keep duplicates — frequency matters

        positive_prefixes = tuple(self._norm(w) for w in self.positive_words)
        negative_prefixes = tuple(self._norm(w) for w in self.negative_words)
        positive_count = sum(1 for word in words if word.startswith(positive_prefixes))
        negative_count = sum(1 for word in words if word.startswith(negative_prefixes))

        # Determine sentiment based on ratio
        total = positive_count + negative_count

        if total == 0:
            return "neutral"

        positive_ratio = positive_count / total

        if positive_ratio > 0.6:
            return "positive"
        elif positive_ratio < 0.4:
            return "negative"
        else:
            return "neutral"

    def extract_topics(self, text: str, max_topics: int = 5) -> List[str]:
        """
        Extract most frequent meaningful words as topics.

        Args:
            text: Text to analyze (should be lowercased)
            max_topics: Maximum number of topics to extract

        Returns:
            List of topic words
        """
        if not text:
            return []

        # Extract words longer than 4 characters
        words = re.findall(r'\w+', text.lower())
        meaningful_words = [
            word for word in words
            if len(word) > 4 and word not in RUSSIAN_STOPWORDS
        ]

        if not meaningful_words:
            return []

        # Count word frequencies
        word_counts = Counter(meaningful_words)

        # Get top N most common words
        top_words = word_counts.most_common(max_topics)

        return [word for word, count in top_words]


class BatchClassifier:
    """Batch classification of multiple documents."""

    def __init__(self):
        """Initialize batch classifier."""
        self.classifier = DocumentClassifier()
        self.logger = get_logger("astrex.classifier")

    def classify_batch(
        self,
        texts: List[Tuple[str, str]]
    ) -> Dict[str, ClassificationResult]:
        """
        Classify multiple documents.

        Args:
            texts: List of (id, text) pairs

        Returns:
            Dictionary mapping id to ClassificationResult
        """
        results = {}

        self.logger.info(f"Starting batch classification of {len(texts)} documents")

        for doc_id, text in texts:
            try:
                result = self.classifier.classify(text)
                results[doc_id] = result
            except Exception as e:
                self.logger.error(f"Error classifying document {doc_id}: {e}")
                # Add a default result for failed classification
                results[doc_id] = ClassificationResult(
                    category="общий",
                    confidence=0.0,
                    language="unknown",
                    sentiment="neutral",
                    topics=[],
                    word_count=0,
                    char_count=0
                )

        self.logger.info(f"Completed batch classification: {len(results)} results")

        return results

    def get_distribution(
        self,
        results: Dict[str, ClassificationResult]
    ) -> Dict[str, int]:
        """
        Get category distribution from classification results.

        Args:
            results: Dictionary of classification results

        Returns:
            Dictionary mapping category to count
        """
        distribution = defaultdict(int)

        for result in results.values():
            distribution[result.category] += 1

        self.logger.debug(f"Category distribution: {dict(distribution)}")

        return dict(distribution)


__all__ = ["DocumentClassifier", "ClassificationResult", "BatchClassifier"]
