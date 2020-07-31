import itertools as it
from functools import reduce
from statistics import mean, variance, stdev, median

from clumper.decorators import return_value_if_empty, grouped, dict_collection_only


class Clumper:
    """
    This object adds methods to a list of dictionaries that make
    it nicer to explore.

    Usage:

    ```python
    from clumper import Clumper

    list_dicts = [{'a': 1}, {'a': 2}, {'a': 3}, {'a': 4}]

    c = Clumper(list_dicts)
    ```
    """

    def __init__(self, blob, groups=tuple()):
        self.blob = blob.copy()
        self.groups = groups

    def __len__(self):
        return len(self.blob)

    def __iter__(self):
        return self.blob.__iter__()

    def __repr__(self):
        return f"<Clumper groups={self.groups} len={len(self)} @{hex(id(self))}>"

    def create_new(self, blob):
        """
        Creates a new collection of data while preserving settings of the
        current collection (most notably, `groups`).
        """
        return Clumper(blob, groups=self.groups)

    def group_by(self, *cols):
        """
        Sets a group on this clumper object or overrides a previous setting.
        A group will affect how some verbs behave. You can undo this behavior
        with `.ungroup()`.

        ![](../img/groupby.png)
        """
        self.groups = cols
        return self

    def ungroup(self):
        """
        Removes all grouping from the collection.

        ![](../img/ungroup.png)
        """
        self.groups = tuple()
        return self

    @grouped
    @dict_collection_only
    def transform(self, **kwargs):
        """
        Does an aggregation just like `.agg()` however instead of reducing the rows we
        merge the results back with the original data. This saves a lot of compute time
        because effectively this prevents us from performing a join.

        Arguments:
            kwargs: keyword arguments that represent the aggregation that is about to happen, see usage below.

        Usage:

        ```python
        from clumper import Clumper

        data = [
            {"a": 1, "b": 1},
            {"a": 1, "b": 2},
            {"a": 2, "b": 5},
            {"a": 2, "b": 5}
        ]

        tfm_data = (Clumper(data)
          .group_by("a")
          .transform(b_sum=("b", "sum"),
                     b_uniq=("b", "unique"))
          .collect())

        tfm_expected = [
            {'a': 1, 'b': 1, 'b_sum': 3, 'b_uniq': [1, 2]},
            {'a': 1, 'b': 2, 'b_sum': 3, 'b_uniq': [1, 2]},
            {'a': 2, 'b': 5, 'b_sum': 10, 'b_uniq': [5]},
            {'a': 2, 'b': 5, 'b_sum': 10, 'b_uniq': [5]}
        ]

        assert tfm_data == tfm_expected
        ```
        """
        agg_results = self.agg(**kwargs)
        return self.left_join(agg_results, mapping={k: k for k in self.groups})

    @staticmethod
    def _merge_dicts(d1, d2, mapping, suffix1, suffix2):
        """
        Merge two dictionaries together. Keeping suffixes in mind.
        """
        map_keys = list(mapping.keys()) + list(mapping.values())
        keys_to_suffix = [
            k for k in set(d1.keys()).intersection(set(d2.keys())) if k not in map_keys
        ]
        d1_new = {(k + suffix1 if k in keys_to_suffix else k): v for k, v in d1.items()}
        d2_new = {(k + suffix2 if k in keys_to_suffix else k): v for k, v in d2.items()}
        return {**d1_new, **d2_new}

    @dict_collection_only
    def left_join(self, other, mapping, lsuffix="", rsuffix="_joined"):
        """
        Performs a left join on two collections.

        Each item from the left set will appear in the final collection. Only
        some items from the right set may appear if a merge is possible. There
        may be multiple copies of the left set if it can be joined multiple times.
        """
        result = []
        # This is a naive implementation. Speedup seems possible.
        for d_i in self:
            values_i = [d_i[k] for k in mapping.keys() if k in d_i.keys()]
            d_i_added = False
            for d_j in other:
                values_j = [d_j[k] for k in mapping.values() if k in d_j.keys()]
                if len(mapping) == len(values_i) == len(values_j):
                    if values_i == values_j:
                        result.append(
                            Clumper._merge_dicts(d_i, d_j, mapping, lsuffix, rsuffix)
                        )
                        d_i_added = True
            if not d_i_added:
                result.append(d_i)
        return self.create_new(result)

    @dict_collection_only
    def inner_join(self, other, mapping, lsuffix="", rsuffix="_joined"):
        """
        Performs an inner join on two collections.

        Any item A in the left set will only appear if there is an item B from
        the right set that it can join on. And vise versa.
        """
        result = []
        # This is a naive implementation. Speedup seems possible.
        for d_i in self:
            values_i = [d_i[k] for k in mapping.keys() if k in d_i.keys()]
            for d_j in other:
                values_j = [d_j[k] for k in mapping.values() if k in d_j.keys()]
                if len(mapping) == len(values_i) == len(values_j):
                    if values_i == values_j:
                        result.append(
                            Clumper._merge_dicts(d_i, d_j, mapping, lsuffix, rsuffix)
                        )
        return self.create_new(result)

    @property
    def only_has_dictionaries(self):
        return all([isinstance(d, dict) for d in self])

    @dict_collection_only
    @grouped
    def agg(self, **kwargs):
        """
        Does an aggregation on a collection of dictionaries. If there are no groups active
        then this method will create a single dictionary containing a summary. If there are
        groups active then the dataset will first split up, then apply the summaries after
        which everything is combined again into a single collection.

        When defining a summary to apply you'll need to pass three things:

        1. the name of the new key
        2. the key you'd like to summarise (first item in the tuple)
        3. the summary you'd like to calculate on that key (second item in the tuple)

        It can also accept a string and it will try to fetch an appropriate function
        for you. If you pass a string it must be either: `mean`, `count`, `unique`,
        `n_unique`, `sum`, `min`, `max`, `median`, `var` or `std`.

        ![](../img/split-apply-combine.png)

        Arguments:
            kwargs: keyword arguments that represent the aggregation that is about to happen, see usage below.

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [
            {'a': 1, 'b': 2},
            {'a': 2, 'b': 3},
            {'a': 3}
        ]

        (Clumper(list_dicts)
          .agg(mean_a=('a', 'mean'),
               min_b=('b', 'min'),
               max_b=('b', 'max'))
          .collect())

        another_list_dicts = [
            {'a': 1, 'c': 'a'},
            {'a': 2, 'c': 'b'},
            {'a': 3, 'c': 'a'}
        ]

        (Clumper(another_list_dicts)
          .group_by('c')
          .agg(mean_a=('a', 'mean'),
               uniq_a=('a', 'unique'))
          .collect())
        ```
        """
        res = {
            name: self.summarise_col(func_str, col)
            for name, (col, func_str) in kwargs.items()
        }
        return Clumper([res], groups=self.groups)

    @dict_collection_only
    def _subsets(self):
        """
        Subsets the data into groups, specified by `.group_by()`.
        """
        result = []
        for gc in self._group_combos():
            subset = self.copy()
            for key, value in gc.items():
                subset = subset.keep(lambda d: d[key] == value)
            result.append(subset)
        return result

    def concat(self, *other):
        """
        Concatenate two or more `Clumper` objects together.

        ![](../img/concat.png)
        """
        return Clumper(self.blob + other.blob)

    def _group_combos(self):
        """
        Returns a dictionary of group-value/clumper pairs.
        """
        combinations = [
            comb for comb in it.product(*[self.unique(c) for c in self.groups])
        ]
        return [{k: v for k, v in zip(self.groups, comb)} for comb in combinations]

    def keep(self, *funcs):
        """
        Allows you to select which items to keep and which items to remove.

        ![](../img/keep.png)

        Arguments:
            funcs: functions that indicate which items to keep

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [{'a': 1}, {'a': 2}, {'a': 3}, {'a': 4}]

        (Clumper(list_dicts)
          .keep(lambda d: d['a'] >= 3)
          .collect())
        ```
        """
        data = self.blob.copy()
        for func in funcs:
            data = [d for d in data if func(d)]
        return self.create_new(data)

    def head(self, n=5):
        """
        Selects the top `n` items from the collection.

        ![](../img/head.png)

        Arguments:
            n: the number of items to grab

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [{'a': 1}, {'a': 2}, {'a': 3}, {'a': 4}]

        (Clumper(list_dicts)
          .head(2)
          .collect())
        ```
        """
        if not isinstance(n, int):
            raise ValueError(f"`n` must be a positive integer, got {n}")
        if n < 0:
            raise ValueError(f"`n` must be a positive integer, got {n}")
        n = min(n, len(self))
        return self.create_new([self.blob[i] for i in range(n)])

    def tail(self, n=5):
        """
        Selects the bottom `n` items from the collection.

        ![](../img/tail.png)

        Arguments:
            n: the number of items to grab

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [{'a': 1}, {'a': 2}, {'a': 3}, {'a': 4}]

        (Clumper(list_dicts)
          .tail(2)
          .collect())
        ```
        """
        if not isinstance(n, int):
            raise ValueError(f"`n` must be a positive integer, got {n}")
        if n < 0:
            raise ValueError(f"`n` must be positive, got {n}")
        n = min(n, len(self))
        return self.create_new([self.blob[-i] for i in range(len(self) - n, len(self))])

    @dict_collection_only
    def select(self, *keys):
        """
        Selects a subset of the keys in each item in the collection.

        ![](../img/select.png)

        Arguments:
            keys: the keys to keep

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [
            {'a': 1, 'b': 2},
            {'a': 2, 'b': 3, 'c':4},
            {'a': 1, 'b': 6}]

        (Clumper(list_dicts)
          .select('a', 'b')
          .collect())
        ```
        """
        return self.create_new([{k: d[k] for k in keys} for d in self.blob])

    @dict_collection_only
    def drop(self, *keys):
        """
        Removes a subset of keys from each item in the collection.

        ![](../img/drop.png)

        Arguments:
            keys: the keys to remove

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [
            {'a': 1, 'b': 2},
            {'a': 2, 'b': 3, 'c':4},
            {'a': 1, 'b': 6}]

        (Clumper(list_dicts)
          .drop('a', 'c')
          .collect())
        ```
        """
        return self.create_new(
            [{k: v for k, v in d.items() if k not in keys} for d in self.blob]
        )

    @grouped
    def mutate(self, **kwargs):
        """
        Adds or overrides key-value pairs in the collection of dictionaries.

        ![](../img/mutate.png)

        Arguments:
            kwargs: keyword arguments of keyname/function-pairs

        Warning:
            This method is aware of groups. There may be different results if a group is active.

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [
            {'a': 1, 'b': 2},
            {'a': 2, 'b': 3, 'c':4},
            {'a': 1, 'b': 6}]

        (Clumper(list_dicts)
          .mutate(c=lambda d: d['a'] + d['b'],
                  s=lambda d: d['a'] + d['b'] + d['c'])
          .collect())
        ```
        """
        data = []
        for d in self.blob.copy():
            new = {k: v for k, v in d.items()}
            for key, func in kwargs.items():
                new[key] = func(new)
            data.append(new)
        return self.create_new(data)

    @grouped
    def sort(self, key, reverse=False):
        """
        Allows you to sort the collection of dictionaries.

        ![](../img/sort.png)

        Arguments:
            key: the number of items to grab
            reverse: the number of items to grab

        Warning:
            This method is aware of groups. Expect different results if a group is active.

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [
            {'a': 1, 'b': 2},
            {'a': 3, 'b': 3},
            {'a': 2, 'b': 1}]

        (Clumper(list_dicts)
          .sort(lambda d: d['a'])
          .collect())

        (Clumper(list_dicts)
          .sort(lambda d: d['b'], reverse=True)
          .collect())
        ```
        """
        return self.create_new(sorted(self.blob, key=key, reverse=reverse))

    def map(self, func):
        """
        Directly map one item to another one using a function.
        If you're dealing with dictionaries, consider using
        `mutate` instead.

        ![](../img/map.png

        Arguments:
            func: the function that will map each item

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [{'a': 1}, {'a': 2}]

        (Clumper(list_dicts)
          .map(lambda d: {'a': d['a'], 'b': 1})
          .collect())
        ```
        """
        return self.create_new([func(d) for d in self.blob])

    @dict_collection_only
    def keys(self, overlap=False):
        """
        Returns all the keys of all the items in the collection.

        Arguments:
            overlap: if `True` only return the keys that overlap in each set

        Usage:

        ```python
        from clumper import Clumper

        data = [{'a': 1, 'b': 2}, {'a': 2, 'c': 3}]

        assert set(Clumper(data).keys(overlap=True)) == {'a'}
        assert set(Clumper(data).keys(overlap=False)) == {'a', 'b', 'c'}
        ```
        """
        if overlap:
            all_keys = [set(d.keys()) for d in self]
            return list(reduce(lambda a, b: a.intersection(b), all_keys))
        return list({k for d in self for k in d.keys()})

    @dict_collection_only
    def explode(self, *to_explode, **kwargs):
        """
        Turns a list in an item into multiple items. The opposite of `.implode()`.

        Arguments:
            to_explode: keys to explode, will keep the same name
            kwargs: (new name, keys to explode)-pairs

        Usage:

        ```python
        from clumper import Clumper

        data = [{'a': 1, 'items': [1, 2]}]

        new_data = Clumper(data).explode("items").collect()
        assert new_data == [{'a': 1, 'items': 1}, {'a': 1, 'items': 2}]

        new_data = Clumper(data).explode(item="items").collect()
        assert new_data == [{'a': 1, 'item': 1}, {'a': 1, 'item': 2}]
        ```
        """
        # you can keep the same name by just using *args or overwrite using **kwargs
        kwargs = {**kwargs, **{k: k for k in to_explode}}
        new_name, to_explode = kwargs.keys(), kwargs.values()

        res = []
        for d in self.blob:
            combinations = it.product(*[d[v] for v in to_explode])
            for comb in combinations:
                new_dict = d.copy()
                for k, v in zip(new_name, comb):
                    new_dict[k] = v
                res.append(new_dict)
        return self.create_new(res).drop(*[k for k in to_explode if k not in new_name])

    def reduce(self, **kwargs):
        """
        Reduce the collection using reducing functions.

        ![](../img/reduce.png)

        Arguments:
            kwargs: key-function pairs

        Usage:

        ```python
        from clumper import Clumper

        list_ints = [1, 2, 3, 4, 5]

        (Clumper(list_ints)
          .reduce(sum_a = lambda x,y: x + y,
                  min_a = lambda x,y: min(x, y),
                  max_a = lambda x,y: max(x, y))
          .collect())
        ```
        """
        return self.create_new(
            [{k: reduce(func, [b for b in self.blob]) for k, func in kwargs.items()}]
        )

    def pipe(self, func, *args, **kwargs):
        """
        Applies a function to the `Clumper` object in a chain-able manner.

        Arguments:
            func: function to apply
            args: arguments that will be passed to the function
            kwargs: keyword-arguments that will be passed to the function

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [{'a': i} for i in range(100)]

        def remove_outliers(clump, min_a=20, max_a=80):
            return (clump
                      .keep(lambda d: d['a'] >= min_a,
                            lambda d: d['a'] <= max_a))

        (Clumper(list_dicts)
          .pipe(remove_outliers, min_a=10, max_a=90)
          .collect())
        ```
        """
        return func(self, *args, **kwargs)

    def collect(self):
        """
        Returns a list instead of a `Clumper` object.

        ![](../img/collect.png)
        """
        return self.blob

    def copy(self):
        """
        Makes a copy of the collection.

        ![](../img/copy.png)

        Usage:

        ```python
        from clumper import Clumper

        list_dicts = [{'a': i} for i in range(100)]

        c1 = Clumper(list_dicts)
        c2 = c1.copy()
        assert id(c1) != id(c2)
        ```
        """
        return self.create_new([d for d in self.blob])

    def summarise_col(self, func, key):
        """
        Apply your own summary function to a key in the collection.

        It can also accept a string and it will try to fetch an appropriate function
        for you. If you pass a string it must be either: `mean`, `count`, `unique`,
        `n_unique`, `sum`, `min`, `max`, `median`, `var` or `std`.

        Note that this method **ignores groups**. It also does not return a `Clumper`
        collection.
        """
        funcs = {
            "mean": mean,
            "count": lambda d: len(d),
            "unique": lambda d: list(set(d)),
            "n_unique": lambda d: len(set(d)),
            "sum": sum,
            "min": min,
            "max": max,
            "median": median,
            "var": variance,
            "std": stdev,
        }
        if isinstance(func, str):
            if func not in funcs.keys():
                raise ValueError(
                    f"Passed `func` must be in {funcs.keys()}, got {func}."
                )
            func = funcs[func]
        return func([d[key] for d in self if key in d.keys()])

    @dict_collection_only
    @return_value_if_empty(value=None)
    def sum(self, col):
        """
        Give the sum of the values that belong to a key.

        ![](../img/sum.png)

        Usage:

        ```python
        from clumper import Clumper

        list_of_dicts = [
            {'a': 7},
            {'a': 2, 'b': 7},
            {'a': 3, 'b': 6},
            {'a': 2, 'b': 7}
        ]

        Clumper(list_of_dicts).sum("a")
        Clumper(list_of_dicts).sum("b")
        ```
        """
        return self.summarise_col(sum, col)

    @dict_collection_only
    @return_value_if_empty(value=None)
    def mean(self, col):
        """
        Give the mean of the values that belong to a key.

        ![](../img/mean.png)

        Usage:

        ```python
        from clumper import Clumper

        list_of_dicts = [
            {'a': 7},
            {'a': 2, 'b': 7},
            {'a': 3, 'b': 6},
            {'a': 2, 'b': 7}
        ]

        assert round(Clumper(list_of_dicts).mean("a"), 1) == 3.5
        assert round(Clumper(list_of_dicts).mean("b"), 1) == 6.7
        ```
        """
        return self.summarise_col(mean, col)

    @dict_collection_only
    @return_value_if_empty(value=0)
    def count(self, col):
        """
        Counts how often a key appears in the collection.

        ![](../img/count.png)

        Usage:

        ```python
        from clumper import Clumper

        list_of_dicts = [
            {'a': 7},
            {'a': 2, 'b': 7},
            {'a': 3, 'b': 6},
            {'a': 2, 'b': 7}
        ]

        assert Clumper(list_of_dicts).count("a") == 4
        assert Clumper(list_of_dicts).count("b") == 3
        ```
        """
        return self.summarise_col(len, col)

    @dict_collection_only
    @return_value_if_empty(value=0)
    def n_unique(self, col):
        """
        Returns number of unique values that a key has.

        ![](../img/n_unique.png)

        Usage:

        ```python
        from clumper import Clumper

        list_of_dicts = [
            {'a': 7},
            {'a': 2, 'b': 7},
            {'a': 3, 'b': 6},
            {'a': 2, 'b': 7}
        ]

        assert Clumper(list_of_dicts).n_unique("a") == 3
        assert Clumper(list_of_dicts).n_unique("b") == 2
        ```
        """
        return self.summarise_col(lambda d: len(set(d)), col)

    @dict_collection_only
    @return_value_if_empty(value=None)
    def min(self, col):
        """
        Returns minimum value that a key has.

        ![](../img/min.png)

        Usage:

        ```python
        from clumper import Clumper

        list_of_dicts = [
            {'a': 7},
            {'a': 2, 'b': 7},
            {'a': 3, 'b': 6},
            {'a': 2, 'b': 7}
        ]

        assert Clumper(list_of_dicts).min("a") == 2
        assert Clumper(list_of_dicts).min("b") == 6
        ```
        """
        return self.summarise_col(min, col)

    @dict_collection_only
    @return_value_if_empty(value=None)
    def max(self, col):
        """
        Returns maximum value that a key has.

        ![](../img/max.png)

        Usage:

        ```python
        from clumper import Clumper

        list_of_dicts = [
            {'a': 7},
            {'a': 2, 'b': 7},
            {'a': 3, 'b': 6},
            {'a': 2, 'b': 7}
        ]

        assert Clumper(list_of_dicts).max("a") == 7
        assert Clumper(list_of_dicts).max("b") == 7
        ```
        """
        return self.summarise_col(max, col)

    @dict_collection_only
    @return_value_if_empty(value=[])
    def unique(self, col):
        """
        Returns a set of unique values that a key has.

        ![](../img/unique.png)

        Usage:

        ```python
        from clumper import Clumper

        list_of_dicts = [
            {'a': 7},
            {'a': 2, 'b': 7},
            {'a': 3, 'b': 6},
            {'a': 2, 'b': 7}
        ]

        assert Clumper(list_of_dicts).unique("a") == [2, 3, 7]
        assert Clumper(list_of_dicts).unique("b") == [6, 7]
        ```
        """
        return self.summarise_col(lambda d: list(set(d)), col)
