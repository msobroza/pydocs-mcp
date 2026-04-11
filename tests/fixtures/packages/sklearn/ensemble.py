"""Ensemble methods: bagging, boosting, and voting."""


class RandomForestClassifier:
    """A random forest classifier.

    A random forest is a meta estimator that fits a number of decision tree
    classifiers on various sub-samples of the dataset and uses averaging
    to improve the predictive accuracy and control over-fitting.

    Parameters
    ----------
    n_estimators : int, default=100
        The number of trees in the forest.
    max_depth : int, default=None
        The maximum depth of the tree.
    random_state : int, default=None
        Controls randomness of the estimator.
    """

    def __init__(self, n_estimators=100, max_depth=None, random_state=None):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.random_state = random_state

    def fit(self, X, y):
        """Build a forest of trees from the training set (X, y).

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training input samples.
        y : array-like of shape (n_samples,)
            Target values.
        """
        return self

    def predict(self, X):
        """Predict class for X.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Input samples.

        Returns
        -------
        y : ndarray of shape (n_samples,)
            The predicted classes.
        """
        return []


class GradientBoostingClassifier:
    """Gradient Boosting for classification.

    This algorithm builds an additive model in a forward stage-wise fashion.

    Parameters
    ----------
    n_estimators : int, default=100
        The number of boosting stages to perform.
    learning_rate : float, default=0.1
        Shrinks the contribution of each tree.
    max_depth : int, default=3
        Maximum depth of individual regression estimators.
    """

    def __init__(self, n_estimators=100, learning_rate=0.1, max_depth=3):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth

    def fit(self, X, y):
        """Fit the gradient boosting model."""
        return self

    def predict(self, X):
        """Predict class labels for samples in X."""
        return []
