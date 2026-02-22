"""
Document classification module for ASTREX v3.

Provides text classification, language detection, sentiment analysis,
and topic extraction capabilities.
"""

import re
from typing import List, Dict, Tuple, Set
from dataclasses import dataclass, field
from collections import Counter, defaultdict

from .logging_setup import get_logger

logger = get_logger(__name__)


# Simple Russian stopwords set
RUSSIAN_STOPWORDS: Set[str] = {
    "и", "в", "на", "с", "по", "не", "что", "это", "как", "для",
    "из", "за", "к", "о", "от", "до", "или", "но", "а", "у",
    "при", "так", "же", "бы", "был", "была", "было", "были",
    "быть", "есть", "была", "будет", "более", "эти", "этот",
    "было", "весь", "вся", "все", "который", "которая", "которые"
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
        self.logger = get_logger(self.__class__.__name__)

        # Predefined categories with Russian keyword patterns
        self.categories = {
            "финансы": [
                "оплат", "счёт", "баланс", "перевод", "сумма", "рубл",
                "доллар", "банк", "бюджет", "прибыль", "убыт"
            ],
            "юридический": [
                "договор", "контракт", "иск", "суд", "закон", "право",
                "устав", "лицензи", "протокол"
            ],
            "кадры": [
                "сотрудник", "зарплат", "увольн", "приём", "отпуск",
                "больнич", "трудов"
            ],
            "техническая": [
                "сервер", "код", "API", "база данных", "deploy", "git", "docker"
            ],
            "переписка": [
                "уважаем", "здравствуйте", "с уважением", "добрый день",
                "письмо", "ответ"
            ],
            "отчёт": [
                "отчёт", "итог", "результат", "анализ", "показатель",
                "динамик", "квартал"
            ]
        }

        # Sentiment keywords
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

        text_lower = text.lower()
        words = re.findall(r'\w+', text_lower)
        total_words = len(words)

        # Calculate scores for each category
        scores = {}
        for category, keywords in self.categories.items():
            count = 0
            for keyword in keywords:
                if ' ' in keyword:
                    # Multi-word phrase: search in full text (word tokens won't contain spaces)
                    count += text_lower.count(keyword)
                else:
                    # Single-word prefix: count tokens that contain the keyword as substring
                    count += sum(1 for word in words if keyword in word)

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
        topics = self.extract_topics(text_lower)

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

        words = re.findall(r'\w+', text.lower())  # keep duplicates — frequency matters for sentiment

        # Count positive and negative word matches
        positive_count = 0
        negative_count = 0

        for word in words:
            # Check if any positive keyword is in the word
            for pos_keyword in self.positive_words:
                if pos_keyword in word:
                    positive_count += 1
                    break

            # Check if any negative keyword is in the word
            for neg_keyword in self.negative_words:
                if neg_keyword in word:
                    negative_count += 1
                    break

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
        self.logger = get_logger(self.__class__.__name__)

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
