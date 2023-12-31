# This file contains code for suporting addressing questions in the data

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.decomposition import PCA
from dateutil.relativedelta import relativedelta
import fynesse.access as access
import fynesse.assess as assess

TAGS = [("amenity", "school")]


def predict_price(latitude, longitude, date, property_type, date_range=28, area_range=0.02, ridge=False, penalty=0, verbose=True):
    latitude = float(latitude)
    longitude = float(longitude)
    try:
        if verbose:
            print('Collecting training samples')
        samples = get_training_samples(latitude, longitude, date, property_type, date_range=date_range, area_range=area_range, limit=500)
        target_id = ((samples.latitude.between(latitude-1e-7, latitude+1e-7)) & (samples.longitude.between(longitude-1e-7, longitude+1e-7)) & (samples.date_of_transfer == date) & (samples.property_type == property_type)).idxmax()
        if verbose:
            print('Number of training samples:', len(samples))
            print('Extracting pois')
        pois_by_features = []
        for i in range(len(samples)):
            pois_by_features.append(assess.count_pois_by_features(assess.get_pois(float(samples.iloc[i].latitude), float(samples.iloc[i].longitude), 
                                                                                assess.KEYS_DICT, box_height=0.01, box_width=0.01), assess.KEYS_DICT, TAGS))
        encoded_property_features = property_feature_map(samples)
        features = convert_to_principle_components(pois_by_features, encoded_property_features)
        target_features = features[target_id]
        features = np.delete(features, target_id, axis=0) # removing target house features from training set
        prices = samples['price'].to_numpy()
        target_price = prices[target_id]
        prices = np.delete(prices, target_id, axis=0)
        avg_percent_error, corr = cross_val(prices, features, ridge, penalty)
        m_linear = sm.OLS(prices, features)
        results = m_linear.fit_regularized(alpha=penalty, L1_wt=0)
        prediction = results.predict(target_features)[0]
        percentage_error = 100 * abs(prediction - target_price) / target_price
        if verbose:
            print(f"Predicted: {prediction}, Actual: {target_price}, Percentage error: {percentage_error}%, Average percentage error: {avg_percent_error}, Model correlation: {corr}")
        return (prediction, target_price, avg_percent_error, corr)
    except Exception as e:
        print(f"The following error occured whilst trying to make a prediction: {e}")

# Returns the percentage error for each training item if it was not included in training and then returns the average
# of this (expected to be higher than prediction error given that bounding box and date range are centered on the actual target).
# Also computes the correlation of the predicted prices and the actual_prices
def cross_val(prices, features, ridge, penalty):
    predictions = np.zeros(len(prices))
    percentage_errors = []
    for i, target_features in enumerate(features):
        target_price = prices[i]
        train_features = np.delete(features, i, axis=0)
        train_prices = np.delete(prices, i, axis=0)
        m_linear = sm.OLS(train_prices, train_features)
        results = m_linear.fit_regularized(alpha=penalty, L1_wt=0)
        prediction = results.predict(target_features)[0]
        predictions[i] = prediction
        percentage_error = 100 * abs(prediction - target_price) / target_price
        percentage_errors.append(percentage_error)
    corr = np.corrcoef(prices, predictions)[0][1]
    avg_pct_error = sum(percentage_errors) / len(percentage_errors)
    return avg_pct_error, corr
    
# get relevant training samples
# will include the target so make sure to remove that before training
def get_training_samples(latitude, longitude, date, property_type, date_range=28, area_range=0.02, limit=1000):
    credentials = access.get_credentials("credentials.yaml")
    conn = access.create_connection(user=credentials["username"], password=credentials["password"], host=credentials["url"], port=credentials["port"], database=credentials["name"])
    conditions = [
                  access.greater_equal_condition('date_of_transfer', f"'{date-relativedelta(days=date_range)}'"), access.greater_equal_condition(f"'{date+relativedelta(days=date_range)}'", 'date_of_transfer'),
                  access.greater_equal_condition('latitude', latitude-area_range), access.greater_equal_condition(latitude+area_range, 'latitude'),
                  access.greater_equal_condition('longitude', longitude-area_range), access.greater_equal_condition(longitude+area_range, 'longitude'),
                  ]
    samples = access.price_coordinates_data_to_df(access.query_table(conn, 'prices_coordinates_data', conditions=conditions, limit=limit))
    conn.close()
    samples = samples[samples.property_type == property_type]
    samples = samples.reset_index()
    return samples

def convert_to_principle_components(pois_by_features, encoded_property_features, threshold=0.95):
    df = pd.DataFrame(pois_by_features)
    df = pd.concat([df, encoded_property_features],axis=1)
    corr = df.corr()
    corr = corr.dropna(how='all')
    corr = corr.dropna(axis=1, how='all')
    dropped_features = df.columns.difference(corr.columns).tolist()
    df = df.drop(columns=dropped_features)
    pca = PCA(n_components=len(corr))
    pca.fit_transform(corr)
    explained_variance = np.cumsum(pca.explained_variance_ratio_)
    cutoff = np.argmax(explained_variance > threshold) + 1
    return pca.transform(df)[:,:cutoff]

def property_feature_map(training_rows):
    replacements = {'new_build_flag': {'N': 0, 'Y' :1}, 'tenure_type': {'L': 0, 'F': 1}}
    res = training_rows[['new_build_flag', 'tenure_type']]
    res = res.replace(replacements)
    res.rename(columns={"tenure_type": 'freehold_flag'})
    return res
        
def convert_property_to_feature_vec(property_feature):
    return np.concatenate((property_feature[0],[property_feature[1], property_feature[2]]))