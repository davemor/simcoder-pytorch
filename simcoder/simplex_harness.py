# Runs the simplex point experiment
# Assumes data is in /Volumes/Data

import pandas as pd
import numpy as np
from typing import List
import math
from nsimplex import NSimplex
from similarity import getDists
from count_cats import getBestCatsInSubset
from count_cats import get_best_cat_index
from scipy.spatial.distance import pdist, squareform
from count_cats import count_number_in_results_in_cat
from scipy.spatial.distance import pdist, squareform
import sys
# nasty import hack - this is a code smell, work out how to remove it
sys.path.append('../')


def euclid_scalar(p1: np.array, p2: np.array):
    distances = math.sqrt(np.sum((p1 - p2) ** 2))
    return distances


def getQueries(categories: np.array, sm_data: np.array) -> List[int]:
    """Return the most categorical query in each of the supplied categories"""
    results = []
    for cat_required in categories:
        cats = get_best_cat_index(cat_required,sm_data)       # all the data in most categorical order (not efficient!)
        results.append(cats[0]) # just get the most categorical one
    return results

def run_experiment(queries : np.array, top_categories: np.array, data: np.array, sm_data: np.array, threshold: float, nn_at_which_k: int ) -> pd.DataFrame:

    query_indices = []  # used to collect results
    nns_at_k_single = [] # used to collect results
    nns_at_k_poly = [] # used to collect results
    best_single_sums = [] # used to collect results
    best_poly_sums = [] # used to collect results

    for i in range(top_categories.size):
        query = queries[i]
        category = top_categories[i]
        dists = getDists(query, data)
        closest_indices = np.argsort(dists)  # the closest images to the query
        
        best_k_for_one_query = closest_indices[0:nn_at_which_k]  # the k closest indices in data to the query
        best_k_categorical = getBestCatsInSubset(category, best_k_for_one_query, sm_data)  # the closest indices in category order - most peacocky peacocks etc.
        poly_query_indexes = best_k_categorical[0:6]  # These are the indices that might be chosen by a human
        poly_query_data = data[poly_query_indexes]  # the actual datapoints for the queries
        num_poly_queries = len(poly_query_indexes)

        poly_query_distances = np.zeros( (num_poly_queries, 1000 * 1000))  # poly_query_distances is the distances from the queries to the all data
        for j in range(num_poly_queries):
            poly_query_distances[j] = getDists(poly_query_indexes[j], data)

        inter_pivot_distances = squareform(pdist(poly_query_data, metric=euclid_scalar)) # pivot-pivot distance matrix with shape (n_pivots, n_pivots)

        # Simplex Projection
        # First calculate the distances from the queries to all data as we will be needing them again

        nsimp = NSimplex()
        nsimp.build_base(inter_pivot_distances,False)

        # Next, find last coord from the simplex formed by 6 query points

        all_apexes = nsimp._get_apex(nsimp._base,np.transpose(poly_query_distances))
        altitudes = all_apexes[:,num_poly_queries -1] # the heights of the simplex - last coordinate

        closest_indices = np.argsort(altitudes) # the closest images to the apex
        best_k_for_simplex = closest_indices[0:nn_at_which_k]

        # Now want to report results the total count in the category

        encodings_for_best_k_single = sm_data[best_k_for_one_query]  # the alexnet encodings for the best k average single query images
        encodings_for_best_k_average = sm_data[best_k_for_simplex]  # the alexnet encodings for the best 100 poly-query images

        # collect up the results

        query_indices.append( query )
        nns_at_k_single.append( count_number_in_results_in_cat(category, threshold, best_k_for_one_query, sm_data) )
        nns_at_k_poly.append( count_number_in_results_in_cat(category, threshold, best_k_for_simplex, sm_data) )
        best_single_sums.append( np.sum(encodings_for_best_k_single[:, category]) )
        best_poly_sums.append( np.sum(encodings_for_best_k_average[:, category]) )
    # --- end for ---

    # now add the results to a dataframe and return it

    results = {
        "query": queries,
        "nns_at_k_single": nns_at_k_single,
        "nns_at_k_poly": nns_at_k_poly,
        "best_single_sums": best_single_sums,
        "best_poly_sums": best_poly_sums
    }

    return pd.DataFrame(results)
