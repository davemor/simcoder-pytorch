import math

from typing import List
from pathlib import Path
import multiprocessing as mp

import click
import numpy as np
import pandas as pd

from scipy.spatial.distance import pdist, squareform

from simcoder.count_cats import findCatsWithCountMoreThanLessThan, getBestCatsInSubset, get_best_cat_index, count_number_in_results_in_cat, findHighlyCategorisedInDataset, get_topcat
from simcoder.similarity import getDists, load_mf_encodings, load_mf_softmax
from simcoder.msed import msed
from simcoder.nsimplex import NSimplex

# Global constants - all global so that they can be shared amongst parallel instances

queries = None
top_categories = None
data = None # the resnet 50 encodings
sm_data = None # the softmax data
threshold = None
nn_at_which_k = None # num of records to compare in results

# Functions:

def euclid_scalar(p1: np.array, p2: np.array):
    distances = math.sqrt(np.sum((p1 - p2) ** 2))
    return distances

def getQueries(categories: np.array, sm_data: np.array) -> List[int]:
    """Return the most (0th) categorical query in each of the supplied categories"""
    return get_nth_categorical_query( categories, sm_data, 0)

def get_nth_categorical_query(categories: np.array, sm_data: np.array, n: int) -> List[int]:
    """Return the nth categorical query in each of the supplied categories"""
    results = []
    for cat_required in categories:
        cats = get_best_cat_index(cat_required,sm_data)       # all the data in most categorical order (not efficient!)
        results.append(cats[n]) # just get the nth categorical one
    return results

def fromSimplexPoint(poly_query_distances: np.array, inter_pivot_distances: np.array, nn_dists:  np.array) -> np.array:
    """poly_query_data is the set of reference points with which to build the simplex
       inter_pivot_distances are the inter-pivot distances with which to build the base simplex
       nn_dists is a column vec of distances, each a bit more than the nn distance from each ref to the rest of the data set
       ie the "perfect" intersection to the rest of the set
       Returns a np.array containing the distances"""

    nsimp = NSimplex()
    nsimp.build_base(inter_pivot_distances, False)

    # second param a (B,N)-shaped array containing distances to the N pivots for B objects.
    perf_point = nsimp._get_apex(nsimp._base, nn_dists)    # was projectWithDistances in matlab

    #print("getting dists")

    dists = np.zeros(1000 * 1000)
    for i in range(1000 * 1000):
        distvec = poly_query_distances[:, i]                      # a row vec of distances
        pr = nsimp._get_apex(nsimp._base, np.transpose(distvec))
        dists[i] = euclid_scalar(pr, perf_point)  # is this right - see comment in simplex_peacock on this!

    return dists

def run_mean_point(i : int):
    """This runs an experiment like perfect point below but uses the means of the distances to other pivots as the apex distance"""
    query = queries[i]
    category = top_categories[i]
        
    assert get_topcat(query, sm_data) == category, "Queries and categories must match."

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


    # next line from Italian documentation: README.md line 25
    inter_pivot_distances = squareform(pdist(poly_query_data, metric=euclid_scalar))  # pivot-pivot distance matrix with shape (n_pivots, n_pivots)

    apex_distances = np.mean( inter_pivot_distances, axis=1)

    # Here we set the perfect point to be at the mean inter-pivot distance.
    # mean_ipd = np.mean(inter_pivot_distances)
    # apex_distances = np.full(num_poly_queries,mean_ipd)

    distsToPerf = fromSimplexPoint(poly_query_distances, inter_pivot_distances,apex_distances)  # was multipled by 1.1 in some versions!

    closest_indices = np.argsort(distsToPerf)  # the closest images to the perfect point
    best_k_for_perfect_point = closest_indices[0:nn_at_which_k]

    # Now want to report results the total count in the category

    encodings_for_best_k_single = sm_data[best_k_for_one_query]  # the alexnet encodings for the best k average single query images
    encodings_for_best_k_average = sm_data[best_k_for_perfect_point]  # the alexnet encodings for the best 100 poly-query images

    return query, count_number_in_results_in_cat(category, threshold, best_k_for_one_query, sm_data), count_number_in_results_in_cat(category, threshold, best_k_for_perfect_point, sm_data), np.sum(encodings_for_best_k_single[:, category]), np.sum(encodings_for_best_k_average[:, category])


def run_perfect_point(i: int):
    """This runs an experiment with the the apex distance based on a NN distance from a simplex point"""

    global queries
    global top_categories
    global data
    global sm_data
    global threshold
    global nn_at_which_k

    query = queries[i]
    category = top_categories[i]
    
    assert get_topcat(query, sm_data) == category, "Queries and categories must match."

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

    # Here we will use some estimate of the nn distance to each query to construct a
    # new point in the nSimplex projection space formed by the poly query objects

    nnToUse = 10
    ten_nn_dists = np.zeros(num_poly_queries)

    for i in range(num_poly_queries):
        sortedDists = np.sort(poly_query_distances[i])
        ten_nn_dists[i] = sortedDists[nnToUse]

    # next line from Italian documentation: README.md line 25
    inter_pivot_distances = squareform(pdist(poly_query_data, metric=euclid_scalar))  # pivot-pivot distance matrix with shape (n_pivots, n_pivots)
    distsToPerf = fromSimplexPoint(poly_query_distances, inter_pivot_distances,ten_nn_dists)  # was multipled by 1.1 in some versions!

    closest_indices = np.argsort(distsToPerf)  # the closest images to the perfect point
    best_k_for_perfect_point = closest_indices[0:nn_at_which_k]

    # Now want to report results the total count in the category

    encodings_for_best_k_single = sm_data[best_k_for_one_query]  # the alexnet encodings for the best k average single query images
    encodings_for_best_k_average = sm_data[best_k_for_perfect_point]  # the alexnet encodings for the best 100 poly-query images

    return query, count_number_in_results_in_cat(category, threshold, best_k_for_one_query, sm_data), count_number_in_results_in_cat(category, threshold, best_k_for_perfect_point, sm_data), np.sum(encodings_for_best_k_single[:, category]), np.sum(encodings_for_best_k_average[:, category])

def run_average(i : int):
    """This just uses the average distance to all points from the queries as the distance"""
    
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

    print( "finished running average", i )
    return query, count_number_in_results_in_cat(category, threshold, best_k_for_one_query, sm_data), count_number_in_results_in_cat(category, threshold, best_k_for_average_indices, sm_data), np.sum(encodings_for_best_k_single[:, category]), np.sum(encodings_for_best_k_average[:, category])

def run_simplex(i : int):
    "This creates a simplex and calculates the simplex height for each of the other points and takes the best n to be the query solution"

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

    return query, count_number_in_results_in_cat(category, threshold, best_k_for_one_query, sm_data), count_number_in_results_in_cat(category, threshold, best_k_for_simplex, sm_data), np.sum(encodings_for_best_k_single[:, category]), np.sum(encodings_for_best_k_average[:, category])

def run_msed(i : int):
    "This runs msed for the queries plus the values from the dataset and takes the lowest."

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

    data_size = data.shape[0]

    msed_results = np.zeros(data_size)

    for j in range(data_size):  # all the rows in the dataset
        data_for_j = np.vstack( (poly_query_data, data[j]) )  # add a row
        msed_results[j] = msed(data_for_j)

    closest_indices = np.argsort(msed_results)                  # the closest images to the apex
    best_k_for_msed_indices = closest_indices[0:nn_at_which_k]

    # Now want to report results the total count in the category

    encodings_for_best_k_single = sm_data[best_k_for_one_query]  # the alexnet encodings for the best k average single query images
    encodings_for_best_k_average = sm_data[best_k_for_msed_indices]  # the alexnet encodings for the best 100 poly-query images

    print( "finished running average", i )
    return query, count_number_in_results_in_cat(category, threshold, best_k_for_one_query, sm_data), count_number_in_results_in_cat(category, threshold, best_k_for_msed_indices, sm_data), np.sum(encodings_for_best_k_single[:, category]), np.sum(encodings_for_best_k_average[:, category])

def run_experiment(the_func, experiment_name: str) -> pd.DataFrame:
    "A wrapper to run the experiments - calls the_func and saves the results from a dataframe"

    assert len(queries) == top_categories.size, "Queries and top_categories must be the same size."    
    assert len(queries) == top_categories.size, "Queries and top_categories must be the same size."    

    num_of_experiments = top_categories.size
     
    max_cpus = mp.cpu_count()
    use_cpus = max_cpus // 4

    print(f"Running {experiment_name} on {use_cpus} cpus from max of {max_cpus}")

    with mp.Pool(use_cpus) as p:
        xs = range(0, num_of_experiments)
        tlist = p.map(the_func,xs)

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

    print(f"Finished running {experiment_name}")
    return pd.DataFrame(results)


def saveData( results: pd.DataFrame, expt_name : str, output_path: Path) -> None:
    "Saves the data to the file system and prints an overview"
    print(results.describe())
    results.to_csv(output_path / f"{expt_name}.csv" )

@click.command()
@click.argument("encodings", type=click.Path(exists=False))
@click.argument("softmax", type=click.Path(exists=False))
@click.argument("output_path", type=click.Path(exists=False))
@click.argument("initial_query_index", type=click.INT)
def experiment100(encodings: str, softmax: str, output_path: str, initial_query_index: int ):
    # These are all globals so that they can be shared by the parallel instances

    global data
    global sm_data
    global nn_at_which_k 
    global threshold
    global top_categories
    global queries

    encodings = Path(encodings)
    softmax = Path(softmax)
    output_path = Path(output_path) 

    # Initialisation of globals

    print(f"Loading {encodings} data encodings.")
    data = load_mf_encodings(encodings) # load resnet 50 encodings

    print(f"Loading {softmax} softmax encodings.")
    sm_data = load_mf_encodings(softmax) # load the softmax data

    print("Loaded datasets")

    nn_at_which_k = 100
    number_of_categories_to_test = 1
    threshold = 0.90 # lower threshold than before

    print("Finding highly categorised categories.")
    top_categories,counts = findCatsWithCountMoreThanLessThan(80,195,sm_data,threshold) # at least 80 and at most 195 - 101 cats
    top_categories = top_categories[0: number_of_categories_to_test]  # subset the top categories

    queries = get_nth_categorical_query(top_categories,sm_data,initial_query_index)  # get one query in each category

    # end of Initialisation of globals - not updated after here

    # pp = run_experiment(run_perfect_point,"perfect_point")
    # saveData(pp,"perfect_point",output_path)
    # meanp = run_experiment(run_mean_point,"mean_point")
    # saveData(meanp,"mean_point",output_path)
    # simp = run_experiment(run_simplex,"simplex")
    # saveData(simp,"simplex",output_path)
    # ave = run_experiment(run_average,"average")
    # saveData(ave,"average",output_path)
    msed_res = run_experiment(run_msed,"msed")
    saveData(msed_res,"msed",output_path)
