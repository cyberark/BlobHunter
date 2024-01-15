import argparse
from datetime import date


def create_parser():
    parser = argparse.ArgumentParser(description='The AWS Cloud Post Exploitation framework')
    parser.add_argument('-a', '--app-id', '--app_id', type=str, default=None,
                        help="Azure App id")
    parser.add_argument('-s', '--app-secret', type=str, default=None,
                        help="Azure app secret")
    parser.add_argument('-t', '--tenant', type=str, default=None,
                        help="Azure app tenant")
    parser.add_argument('-o', '--output', type=str, default=f"public-containers-{date.today()}.csv",
                        help="File name or location where to save all results")
    parser.add_argument("-a", "--auto", action="store_true", default=False,
                        help="Proceed scan in fully automated mode, without asking or requesting info. "
                             "Make sure to provide credentials in props")
    return parser.parse_args()


cli_arguments = create_parser()
