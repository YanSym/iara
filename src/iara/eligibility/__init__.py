"""Eligibility module — event normalization and eligibility decisions."""

from iara.eligibility.decision import EligibilityChecker
from iara.eligibility.normalizer import ChatwootEventNormalizer

__all__ = ["ChatwootEventNormalizer", "EligibilityChecker"]
