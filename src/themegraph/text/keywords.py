"""Human-readable topic labels via class-based TF-IDF. Never used as features."""

from __future__ import annotations

import numpy as np
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

# Words that dominate call transcripts and filings without saying what a topic is.
FILLER = {
    "think", "just", "going", "really", "question", "questions", "don", "know", "kind",
    "look", "looking", "right", "yes", "yeah", "okay", "thank", "thanks", "lot", "bit",
    "little", "say", "said", "good", "great", "obviously", "certainly", "actually",
    "maybe", "sure", "want", "mean", "way", "things", "thing", "got", "make", "terms",
    "quarter", "quarters", "year", "years", "half", "second", "fourth", "third", "first",
    "months", "ended", "fiscal", "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december", "million", "billion",
    "percent", "net", "income", "gaap", "non", "operations", "results", "company",
    "business", "products", "customers", "adversely", "affect", "sequentially",
}


def ctfidf_keywords(docs_by_topic: dict[int, str], top_n: int = 8) -> dict[int, list[str]]:
    if not docs_by_topic:
        return {}
    keys = list(docs_by_topic)
    stop = sorted(ENGLISH_STOP_WORDS | FILLER)
    cv = CountVectorizer(stop_words=stop, ngram_range=(1, 2), min_df=1,
                         token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]{2,}\b")
    X = cv.fit_transform([docs_by_topic[k] for k in keys]).astype(float)
    tf = X.multiply(1 / np.maximum(X.sum(axis=1), 1)).tocsr()
    avg = X.sum() / len(keys)
    idf = np.log1p(avg / np.maximum(np.asarray(X.sum(axis=0)).ravel(), 1))
    scores = tf.multiply(idf).tocsr()
    vocab = np.array(cv.get_feature_names_out())
    out = {}
    for i, k in enumerate(keys):
        row = scores.getrow(i).toarray().ravel()
        out[k] = vocab[np.argsort(-row)[:top_n]].tolist()
    return out
