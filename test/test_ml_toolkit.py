import unittest
import numpy as np
import pandas as pd
from numpy import testing as nptest
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import KFold

from operational_analysis.toolkits.machine_learning_setup import MachineLearningSetup

class TestMLToolkit(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        
        # Create 2 random features, 1st to mimic wind speed, the other air density
        self.x1 = pd.Series(np.random.random(1000) * 30) # Wind speed
        self.x2 = pd.Series(np.random.random(1000) * 360) # Wind direction
        self.x3 = pd.Series(1 + np.random.random(1000) * 0.2) # Air density
        
        # Group features together
        self.X = pd.DataFrame(data = {'ws': self.x1, 'wd': self.x2, 'dens': self.x3})

        # Create simple power relationship with feature variables
        self.y = self.x3 * np.power(self.x1,3) * np.log(self.x2) / 6 # Units of kW

    def test_etr(self):
    # Test different algorithms hyperoptimization and fitting results
    # Hyperparameter optimization is based on randomized grid search, so pass criteria is not stringent
    
        algorithms = ['etr', 'gbm', 'gam'] # Define algorithms
        
        # Loop through algorithms
        for a in algorithms:
            ml = MachineLearningSetup(a) # Setup ML object
            
            # Perform randomized grid search only once for efficiency
            ml.hyper_optimize(self.X, self.y, n_iter_search = 1, report = False, cv = KFold(n_splits = 2))
            
            # Predict power based on model results
            y_pred = ml.random_search.predict(self.X)
            
            # Compute performance metrics which we'll test
            corr = np.corrcoef(self.y, y_pred)[0,1] # Correlation between predicted and actual power
            rmse = np.sqrt(mean_squared_error(self.y, y_pred)) # RMSE between predicted and actual power
            
            # Specify desired accuracy of model fit           
            desired_corr = 0.95 # Correlation
            desired_rmse = 2000.0 # RMSE in kW
            
            # Test power sum in GW is within 3 decimal places
            nptest.assert_almost_equal(self.y.sum()/1e6, y_pred.sum()/1e6, decimal = 3, 
                                       err_msg="Sum of predicted and actual power not close enough")
            
            # Test correlation of model fit
            nptest.assert_array_less(desired_corr, corr, 
                                     err_msg="Correlation between features and response not high enough")
            
            # Test RMSE of model fit
            nptest.assert_array_less(rmse, desired_rmse, 
                                     err_msg="RMSE of model fit not low enough")

    def tearDown(self):
        pass
