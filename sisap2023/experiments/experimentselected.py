from dataclasses import dataclass
import math

from typing import List
from pathlib import Path
import multiprocessing as mp

import click
import numpy as np
import pandas as pd

from scipy.spatial.distance import pdist, squareform
from sisap2023.utils.count_cats import (
    countNumberinCatGTThresh,
    count_number_in_results_cated_as,
    findCatsWithCountMoreThanLessThan,
    getBestCatsInSubset,
    get_best_cat_index,
)
from sisap2023.utils.mirflickr import load_encodings
from sisap2023.utils.distances import euclid_scalar, get_dists, l1_norm, l2_norm, relu
from sisap2023.metrics.msedOO import msedOO
from sisap2023.metrics.msed import msed
from sisap2023.metrics.nsimplex import NSimplex, fromSimplexPoint
from sisap2023.metrics.jsd_dist import jsd_dist

# Global constants - all global so that they can be shared amongst parallel instances

queries = None
top_categories = None
data = None  # the resnet 50 encodings
sm_data = None  # the softmax data
threshold = None
nn_at_which_k = None  # num of records to compare in results
categories = None  # The categorical strings
best_k_for_queries = None
num_poly_queries = 6

# Functions:


def get_nth_categorical_query(categories: np.array, sm_data: np.array, n: int) -> List[int]:
    """Return the nth categorical query in each of the supplied categories"""
    results = []
    for cat_required in categories:
        cats = get_best_cat_index(cat_required, sm_data)  # all the data in most categorical order (not efficient!)
        results.append(cats[n])  # just get the nth categorical one
    return results


def select_poly_query_images(i: int, num_poly_queries: int) -> np.array:
    category, best_k_for_one_query = categories[i], best_k_for_queries[i]

    # the closest indices in category order - most peacocky peacocks etc.
    best_k_categorical = getBestCatsInSubset(category, best_k_for_one_query, sm_data)

    poly_query_indexes = best_k_categorical[0:num_poly_queries]  # These are the indices that might be chosen by a human
    poly_query_data = data[poly_query_indexes]
    return poly_query_data, poly_query_indexes


def compute_results(i: int, distances: np.array) -> tuple:
    query, category, best_k_for_one_query = queries[i], categories[i], best_k_for_queries[i]
    closest_indices = np.argsort(distances)  # the closest images
    best_k_for_poly_indices = closest_indices[0:nn_at_which_k]

    encodings_for_best_k_single = sm_data[best_k_for_one_query]
    encodings_for_best_k_poly = sm_data[best_k_for_poly_indices]

    max_possible_in_cat = countNumberinCatGTThresh(category, threshold, sm_data)

    return (
        query,
        max_possible_in_cat,
        category,
        categories[category],
        count_number_in_results_cated_as(category, best_k_for_queries[i], sm_data),
        count_number_in_results_cated_as(category, best_k_for_poly_indices, sm_data),
        np.sum(encodings_for_best_k_single[:, category]),
        np.sum(encodings_for_best_k_poly[:, category]),
    )


def run_cos(i: int):
    """This runs an experiment finding the NNs using cosine distance"""
    query, category, best_k_for_one_query = queries[i], categories[i], best_k_for_queries[i]
    normed_data = l2_norm(data)
    distances = get_dists(query, normed_data)  # cosine distance same order as l2 norm of data
    return compute_results(query, category, sm_data, distances, nn_at_which_k, best_k_for_one_query)


def run_jsd(i: int):
    """This runs an experiment finding the NNs using SED"""
    """Uses the msed implementation"""
    query = queries[i]
    relued_data = relu(data)
    normed_data = l1_norm(relued_data)
    distances = jsd_dist(normed_data[query], normed_data)
    return compute_results(i, distances)


def run_sed(i: int):
    """This runs an experiment finding the NNs using SED"""
    """Uses the msed implementation"""
    query = queries[i]
    distances = np.zeros(1000 * 1000)
    for j in range(1000 * 1000):
        distances[j] = msed(np.vstack((data[query], data[j])))
    return compute_results(i, distances)


def run_mean_point(i: int):
    """This runs an experiment like perfect point below but uses the means of the distances to other pivots as the apex distance"""
    poly_query_data, poly_query_indexes = select_poly_query_images(i)

    # poly_query_distances is the distances from the queries to the all data
    poly_query_distances = np.zeros((num_poly_queries, 1000 * 1000))
    for j in range(num_poly_queries):
        poly_query_distances[j] = get_dists(poly_query_indexes[j], data)

    # next line from Italian documentation: README.md line 25
    # pivot-pivot distance matrix with shape (n_pivots, n_pivots)
    inter_pivot_distances = squareform(pdist(poly_query_data, metric=euclid_scalar))

    apex_distances = np.mean(inter_pivot_distances, axis=1)

    # Here we set the perfect point to be at the mean inter-pivot distance.
    # mean_ipd = np.mean(inter_pivot_distances)
    # apex_distances = np.full(num_poly_queries,mean_ipd)
    # was multipled by 1.1 in some versions!
    distances = fromSimplexPoint(poly_query_distances, inter_pivot_distances, apex_distances)

    return compute_results(i, distances)


def run_perfect_point(i: int):
    """This runs an experiment with the the apex distance based on a NN distance from a simplex point"""
    poly_query_data, poly_query_indexes = select_poly_query_images(i)

    # poly_query_distances is the distances from the queries to the all data
    poly_query_distances = np.zeros((num_poly_queries, 1000 * 1000))
    for j in range(num_poly_queries):
        poly_query_distances[j] = get_dists(poly_query_indexes[j], data)

    # Here we will use some estimate of the nn distance to each query to construct a
    # new point in the nSimplex projection space formed by the poly query objects
    nnToUse = 10
    ten_nn_dists = np.zeros(num_poly_queries)

    for i in range(num_poly_queries):
        sortedDists = np.sort(poly_query_distances[i])
        ten_nn_dists[i] = sortedDists[nnToUse]

    # next line from Italian documentation: README.md line 25
    # pivot-pivot distance matrix with shape (n_pivots, n_pivots)
    inter_pivot_distances = squareform(pdist(poly_query_data, metric=euclid_scalar))
    # was multipled by 1.1 in some versions!
    distances = fromSimplexPoint(poly_query_distances, inter_pivot_distances, ten_nn_dists)

    return compute_results(i, distances)


def run_average(i: int):
    """This just uses the average distance to all points from the queries as the distance"""
    _, poly_query_indexes = select_poly_query_images(i)

    poly_query_distances = np.zeros((num_poly_queries, 1000 * 1000))
    for j in range(num_poly_queries):
        poly_query_distances[j] = get_dists(poly_query_indexes[j], data)

    distances = np.sum(poly_query_distances, axis=0)
    return compute_results(i, distances)


def run_simplex(i: int):
    "This creates a simplex and calculates the simplex height for each of the other points and takes the best n to be the query solution"
    poly_query_data, poly_query_indexes = select_poly_query_images(i)

    # poly_query_distances is the distances from the queries to the all data
    poly_query_distances = np.zeros((num_poly_queries, 1000 * 1000))
    for j in range(num_poly_queries):
        poly_query_distances[j] = get_dists(poly_query_indexes[j], data)

    # pivot-pivot distance matrix with shape (n_pivots, n_pivots)
    inter_pivot_distances = squareform(pdist(poly_query_data, metric=euclid_scalar))

    # Simplex Projection
    # First calculate the distances from the queries to all data as we will be needing them again
    nsimp = NSimplex()
    nsimp.build_base(inter_pivot_distances, False)

    # Next, find last coord from the simplex formed by 6 query points
    all_apexes = nsimp._get_apex(nsimp._base, np.transpose(poly_query_distances))
    altitudes = all_apexes[:, num_poly_queries - 1]  # the heights of the simplex - last coordinate

    return compute_results(i, altitudes)


def run_msed(i: int):
    "This runs msed for the queries plus the values from the dataset and takes the lowest."
    poly_query_data, poly_query_indexes = select_poly_query_images(i)

    relued = relu(data)
    normed_data = l1_norm(relued)

    base = msedOO(np.array(poly_query_data))
    msed_results = base.msed(normed_data)
    msed_results = msed_results.flatten()

    return compute_results(i, msed_results)


def run_experiment(the_func, experiment_name: str, output_path: str):
    "A wrapper to run the experiments - calls the_func and saves the results from a dataframe"

    assert len(queries) == top_categories.size, "Queries and top_categories must be the same size."

    num_of_experiments = top_categories.size

    max_cpus = mp.cpu_count()
    use_cpus = max_cpus // 2

    print(f"Running {experiment_name} on {use_cpus} cpus from max of {max_cpus}")

    with mp.Pool(use_cpus) as p:
        xs = range(0, num_of_experiments)
        tlist = p.map(the_func, xs)

    # tlist is a list of tuples each tuple is the result of one run
    # which look like this: query, count best_k_for_one_query, best_k_for_expt, sum best_k_one, sum best_k_expt
    # now get a tuple of lists:

    unzipped = tuple(list(x) for x in zip(*tlist))
    # unzipped is a list of lists
    # now add the results to a dataframe and return it

    results = {
        "query": unzipped[0],
        "no_in_cat": unzipped[1],
        "cat_index": unzipped[2],
        "cat_string": unzipped[3],
        "nns_at_k_single": unzipped[4],
        "nns_at_k_poly": unzipped[5],
        "best_single_sums": unzipped[6],
        "best_poly_sums": unzipped[7],
    }

    print(f"Finished running {experiment_name}")

    results_df = pd.DataFrame(results)

    print(results_df.describe())
    results_df.to_csv(Path(output_path) / f"{experiment_name}.csv")


def compute_best_k_for_queries(queries: List[int]):
    def closest(query):
        dists = get_dists(query, data)
        closest_indices = np.argsort(dists)
        return closest_indices[0:nn_at_which_k]

    return [closest(q) for q in queries]


@click.command()
@click.argument("encodings", type=click.Path(exists=False))
@click.argument("softmax", type=click.Path(exists=False))
@click.argument("output_path", type=click.Path(exists=False))
@click.argument("number_of_categories_to_test", type=click.INT)
@click.argument("k", type=click.INT)
@click.argument("initial_query_index", type=click.INT)
@click.argument("thresh", type=click.FLOAT)
def experimentselected(
    encodings: str,
    softmax: str,
    output_path: str,
    number_of_categories_to_test: int,
    k: int,
    initial_query_index: int,
    thresh: float,
):
    # These are all globals so that they can be shared by the parallel instances

    global data
    global sm_data
    global nn_at_which_k
    global threshold
    global top_categories
    global queries
    global categories
    global best_k_for_queries

    print("Running experiment100.")
    print(f"encodings: {encodings}")
    print(f"softmax: {softmax}")
    print(f"output_path: {output_path}")
    print(f"initial_query_index: {initial_query_index}")

    # Initialisation of globals

    print(f"Loading {encodings} data encodings.")
    data = load_encodings(Path(encodings))  # load resnet 50 encodings

    print(f"Loading {softmax} softmax encodings.")
    sm_data = load_encodings(Path(softmax))  # load the softmax data

    with open("imagenet_classes.txt", "r") as f:
        categories = [s.strip() for s in f.readlines()]

    print("Loaded datasets")

    nn_at_which_k = k
    threshold = thresh

    print("Finding highly categorised categories.")
    # at least 80 and at most 195 - 101 cats sm values for resnet_50
    top_categories, counts = findCatsWithCountMoreThanLessThan(100, 184, sm_data, threshold)
    top_categories = top_categories[0:number_of_categories_to_test]  # subset the top categories

    with open("selected_queries.txt", "r") as f:
        queries = [int(line.strip()) for line in f]

    queries = get_nth_categorical_query(
        top_categories, sm_data, initial_query_index
    )  # get one query in each categories

    print(queries)

    best_k_for_queries = compute_best_k_for_queries(queries)

    # end of Initialisation of globals - not updated after here

    # run_experiment(run_perfect_point, "perfect_point", output_path)
    # run_experiment(run_mean_point, "mean_point", output_path)
    # run_experiment(run_simplex, "simplex", output_path)
    # run_experiment(run_average, "average", output_path)
    # run_experiment(run_msed, "msed", output_path)
    # run_experiment(run_cos, "cos", output_path)
    # run_experiment(run_sed, "sed", output_path)
    run_experiment(run_jsd, "jsd", output_path)
