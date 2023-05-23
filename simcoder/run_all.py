import functools
import logging
import math
import multiprocessing as mp
from typing import List
import numpy as np
import pandas as pd

from count_cats import findHighlyCategorisedInDataset
from perfect_point_harness import run_perfect_point, getQueries
from similarity import getDists
from simplex_harness import run_simplex
from mean_point_harness import run_mean_point
from count_cats import getBestCatsInSubset
from count_cats import get_best_cat_index
from count_cats import count_number_in_results_in_cat

import time

from pathlib import Path
from similarity import load_mf_encodings
from similarity import load_mf_softmax
# import simcoder.perfect_point_harness as pph # moved below to aid reload
# Load the data:

data_root = Path("/Volumes/Data/")

# Global constants:

queries = None
top_categories = None
data = None # the resnet 50 encodings
sm_data = None # the softmax data
threshold = None
nn_at_which_k = None # num of records to compare in results

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

def run_average(i : int):

    global queries
    global top_categories
    global data
    global sm_data
    global threshold
    global nn_at_which_k
    
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

    row_sums = np.sum(poly_query_distances,axis=0)
    lowest_sum_indices = np.argsort(row_sums)

    best_k_for_average_indices = lowest_sum_indices[:nn_at_which_k]

    # Now want to report results the total count in the category

    encodings_for_best_k_single = sm_data[best_k_for_one_query]  # the alexnet encodings for the best k average single query images
    encodings_for_best_k_average = sm_data[best_k_for_average_indices]  # the alexnet encodings for the best 100 poly-query images

    return query, count_number_in_results_in_cat(category, threshold, best_k_for_one_query, sm_data), count_number_in_results_in_cat(category, threshold, best_k_for_average_indices, sm_data), np.sum(encodings_for_best_k_single[:, category]), np.sum(encodings_for_best_k_average[:, category])


def run_experiment( the_func ) -> pd.DataFrame:

    assert queries.size == top_categories.size, "Queries and top_categories must be the same size."    

    num_of_experiments = top_categories.size
     
    with mp.Pool(mp.cpu_count()) as p:
        xs = range(0, num_of_experiments)
        tlist = p.map(the_func(xs))

    # tlist is a list of tuples each tuple is the result of one run
    # which look like this: query, count best_k_for_one_query, best_k_for_expt, sum best_k_one, sum best_k_expt
    # now get a tuple of lists:

    unzipped = tuple(list(x) for x in zip(*tlist))
    # unzipped is a list of lists
    # now add the results to a dataframe and return it

    results = {
        "query": unzipped[0],
        "nns_at_k_single": unzipped[1],
        "nns_at_k_poly": unzipped[2],
        "best_single_sums": unzipped[3],
        "best_poly_sums": unzipped[4]
    }

    return pd.DataFrame(results)

def saveData( results: pd.DataFrame, expt_name : str,encodings_name: str) -> None:
    print(results.describe())
    results.to_csv(data_root / "results" / f"{expt_name}_{encodings_name}.csv" )

def main():

    global queries
    global top_categories
    global data
    global sm_data
    global threshold
    global nn_at_which_k 

    # encodings_name = 'mf_resnet50'
    encodings_name = 'mf_dino2'
    print(f"Loading {encodings_name} encodings.")
    data = load_mf_encodings(data_root / encodings_name) # load resnet 50 encodings

    print(f"Loading Alexnet Softmax encodings.")
    sm_data = load_mf_encodings(data_root / "mf_alexnet_softmax") # load the softmax data

    print("Loaded datasets")

    start_time = time.time()

    nn_at_which_k = 100
    number_of_categories_to_test = 1
    threshold = 0.95

    print("Finding highly categorised categories.")
    top_categories,counts = findHighlyCategorisedInDataset(sm_data, threshold)  # get the top categories in the dataset
    top_categories = top_categories[0: number_of_categories_to_test]  # subset the top categories

    queries = getQueries(top_categories,sm_data)  # get one query in each category

 #   perp_results = run_experiment(queries, top_categories, data, sm_data, threshold, nn_at_which_k,run_perfect_point )
 #   mean_results = run_experiment(queries, top_categories, data, sm_data, threshold, nn_at_which_k,run_mean_point )
 #   simp_results = run_experiment(queries, top_categories, data, sm_data, threshold, nn_at_which_k,run_simplex )
    aver_results = run_experiment(run_average)

    print("--- %s seconds ---" % (time.time() - start_time))

    # saveData( perp_results,"perfect_point",encodings_name)
    # saveData( mean_results,"mean_point",encodings_name)
    # saveData( simp_results,"simplex",encodings_name)
    # saveData( aver_results,"average",encodings_name)

if __name__ == "__main__":
    mp.set_start_method("fork")
    main()