import os
import importlib
import inspect

from sites.base_site import BaseSite
from core.logger import setup_logger


logger = setup_logger()

def load_sites():

    sites = {}

    sites_folder = os.path.dirname(__file__)

    logger.info("SITE LOADER | scanning sites folder")

    for file in os.listdir(sites_folder):

        if file.endswith("_site.py"):

            module_name = f"sites.{file[:-3]}"

            module = importlib.import_module(module_name)

            for name,obj in inspect.getmembers(module):

                if inspect.isclass(obj) and issubclass(obj,BaseSite) and obj != BaseSite:

                    site = obj()

                    sites[site.name] = site

                    logger.info(f"SITE LOADER | loaded {site.name}")

    return sites