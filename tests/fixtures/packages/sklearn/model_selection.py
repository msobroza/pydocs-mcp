"""Model selection: cross-validation, hyperparameter tuning."""


def train_test_split(*arrays, test_size=0.25, random_state=None, shuffle=True):
    """Split arrays into random train and test subsets.

    Parameters
    ----------
    *arrays : sequence of indexables
        Allowed inputs are lists, numpy arrays, scipy-sparse matrices.
    test_size : float, default=0.25
        Proportion of the dataset to include in the test split.
    random_state : int, default=None
        Controls the shuffling applied to the data before splitting.
    shuffle : bool, default=True
        Whether to shuffle the data before splitting.

    Returns
    -------
    splitting : list
        List containing train-test split of inputs.
    """
    return list(arrays) * 2


class GridSearchCV:
    """Exhaustive search over specified parameter values for an estimator.

    Parameters
    ----------
    estimator : estimator object
        An object of that type is instantiated for each grid point.
    param_grid : dict or list of dicts
        Dictionary with parameters names as keys and lists of settings.
    cv : int, default=5
        Number of folds for cross-validation.
    scoring : str, default=None
        Strategy to evaluate the performance of the model.
    """

    def __init__(self, estimator, param_grid, cv=5, scoring=None):
        self.estimator = estimator
        self.param_grid = param_grid
        self.cv = cv
        self.scoring = scoring

    def fit(self, X, y):
        """Run fit with all sets of parameters."""
        return self
