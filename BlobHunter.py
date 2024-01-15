import itertools
import subprocess
import time

import azure.core.exceptions
import pyinputplus as pyip
from azure.identity import AzureCliCredential
from azure.mgmt.resource import SubscriptionClient, ResourceManagementClient
from azure.mgmt.storage import StorageManagementClient
from azure.storage.blob import BlobServiceClient, ContainerClient

from src.arguments import cli_arguments
from src.constants import STOP_SCAN_FLAG, ENDPOINT_URL, EXTENSIONS, CONTAINER_URL
from src.file_processing import write_csv, delete_csv
from src.logo import print_logo


def get_credentials() -> AzureCliCredential:
    credentials = None
    if cli_arguments.app_id and cli_arguments.app_secret and cli_arguments.tenant:
        username = subprocess.check_output(f"az login --service-principal "
                                           f"-u {cli_arguments.app_id} "
                                           f"-p {cli_arguments.app_secret} "
                                           f"--tenant {cli_arguments.tenant}",
                                           shell=True,
                                           stderr=subprocess.DEVNULL).decode("utf-8")
        credentials = AzureCliCredential()
    if cli_arguments.auto and not credentials:
        raise ConnectionError('Can not log in using provided credentials')
    else:
        try:
            username = subprocess.check_output("az account show --query user.name", shell=True,
                                               stderr=subprocess.DEVNULL).decode("utf-8")
    
        except subprocess.CalledProcessError:
            subprocess.check_output("az login", shell=True, stderr=subprocess.DEVNULL)
            username = subprocess.check_output("az account show --query user.name", shell=True,
                                               stderr=subprocess.DEVNULL).decode("utf-8")
        credentials = AzureCliCredential()
    print("[+] Logged in as user {}".format(username.replace('"', '').replace("\n", '')), flush=True)
    return credentials


def get_tenants_and_subscriptions(creds):
    subscription_client = SubscriptionClient(creds)
    tenants_ids = []
    tenants_names = []
    subscriptions_ids = []
    subscription_names = []

    for sub in subscription_client.subscriptions.list():
        if sub.state == 'Enabled':
            tenants_ids.append(sub.tenant_id)
            subscriptions_ids.append(sub.id[15:])
            subscription_names.append(sub.display_name)

    # Getting tenant name from given tenant id
    for ten_id in tenants_ids:
        tenants_names.extend(
            ten.display_name
            for ten in subscription_client.tenants.list()
            if ten_id == ten.id[9:]
        )
    return tenants_ids, tenants_names, subscriptions_ids, subscription_names


def iterator_wrapper(iterator):
    flag_http_response_code_429 = False
    while True:
        try:
            iterator, iterator_copy = itertools.tee(iterator)
            iterator_value = next(iterator)
            yield iterator_value, None
            flag_http_response_code_429 = False
        except StopIteration as e_stop:
            yield None, e_stop
        except azure.core.exceptions.HttpResponseError as e_http:
            if e_http.status_code == 429:
                wait_time = int(e_http.response.headers["Retry-After"]) + 10
                print(
                    f"[!] Encounter throttling limits error. "
                    f"In order to continue the scan, you need to wait {wait_time} min",
                    flush=True,
                )
                if cli_arguments.auto:
                    print(f"[!] {wait_time} min timer started", flush=True)
                    time.sleep(wait_time)
                else:
                    response = pyip.inputMenu(
                        ['N', 'Y'],
                        f"Do you wish to wait {wait_time} min? or stop the scan here and receive "
                        f"the script outcome till this part\n"
                        f"Enter Y for Yes, Continue the scan\n"
                        f"Enter N for No, Stop the scan \n",
                    )

                    if response == 'Y':
                        print(f"[!] {wait_time} min timer started", flush=True)
                        time.sleep(wait_time)
                    else:
                        yield STOP_SCAN_FLAG, None

                if flag_http_response_code_429:
                    # This means this current iterable object got throttling limit 2 times in a row, this condition 
                    # has been added to prevent an infinite loop of throttling limit.
                    print(
                        "[!] The current object we have been trying to access has triggered throttling limit error 2 "
                        "times in a row, skipping this object ",
                        flush=True)
                    flag_http_response_code_429 = False
                    yield None, e_http
                else:
                    flag_http_response_code_429 = True
                    iterator = iterator_copy
                    continue

            else:
                yield None, e_http
        except Exception as e:
            yield None, e


def check_storage_account(account_name, key):
    blob_service_client = BlobServiceClient(ENDPOINT_URL.format(account_name), credential=key)
    containers = blob_service_client.list_containers(timeout=15)
    public_containers = []

    for cont, e in iterator_wrapper(containers):
        if cont == STOP_SCAN_FLAG:
            break
        if e:
            if type(e) is not StopIteration:
                print(
                    f"\t\t[-] Could not scan the container of the account{account_name} due to the error{e}. skipping",
                    flush=True,
                )
                continue
            else:
                break
        if cont.public_access is not None:
            public_containers.append(cont)

    return public_containers


def check_subscription(tenant_id, tenant_name, sub_id, sub_name, creds):
    print(f"\n\t[*] Checking subscription {sub_name}:", flush=True)

    storage_client = StorageManagementClient(creds, sub_id)

    # Obtain the management object for resources
    resource_client = ResourceManagementClient(creds, sub_id)

    # Retrieve the list of resource groups
    group_list = resource_client.resource_groups.list()
    resource_groups = [group.name for group in list(group_list)]
    print(f"\t\t[+] Found {len(resource_groups)} resource groups", flush=True)
    group_to_names_dict = {group: {} for group in resource_groups}

    accounts_counter = 0
    for group in resource_groups:
        for item, e in iterator_wrapper(storage_client.storage_accounts.list_by_resource_group(group)):
            if item == STOP_SCAN_FLAG:
                break
            if e:
                if type(e) is StopIteration:
                    break
                print(
                    f"\t\t[-] Could not access one of the resources of the group {group}, "
                    f"due to the error {e} skipping the resource",
                    flush=True,
                )
                continue
            accounts_counter += 1
            group_to_names_dict[group][item.name] = ''

    print(f"\t\t[+] Found {accounts_counter} storage accounts", flush=True)

    for group in resource_groups:
        for account in group_to_names_dict[group].keys():
            try:
                storage_keys = storage_client.storage_accounts.list_keys(group, account)
                storage_keys = {v.key_name: v.value for v in storage_keys.keys}
                group_to_names_dict[group][account] = storage_keys['key1']
            except azure.core.exceptions.HttpResponseError:
                print("\t\t[-] User do not have permissions to retrieve storage accounts keys in the given"
                      " subscription", flush=True)
                print("\t\t    Can not scan storage accounts", flush=True)
                return

    output_list = []

    for group in resource_groups:
        for account in group_to_names_dict[group].keys():
            key = group_to_names_dict[group][account]
            public_containers = check_storage_account(account, key)

            for cont in public_containers:
                access_level = cont.public_access
                container_client = ContainerClient(ENDPOINT_URL.format(account), cont.name, credential=key)
                files = [f.name for f in container_client.list_blobs()]
                ext_dict = count_files_extensions(files, EXTENSIONS)
                row = [tenant_id, tenant_name, sub_id, sub_name, group, account, cont.name, access_level,
                       CONTAINER_URL.format(account, cont.name), len(files)]

                row.extend(ext_dict[ext] for ext in ext_dict.keys())
                output_list.append(row)

    print("\t\t[+] Scanned all storage accounts successfully", flush=True)

    if output_list:
        print(f"\t\t[+] Found {len(output_list)} PUBLIC containers", flush=True)
    else:
        print("\t\t[+] No PUBLIC containers found")

    header = ["Tenant ID", "Tenant Name", "Subscription ID", "Subscription Name", "Resource Group", "Storage Account",
              "Container",
              "Public Access Level", "URL", "Total Files"]

    header.extend(iter(EXTENSIONS))
    header.append("others")
    write_csv(cli_arguments.output, header, output_list)


def count_files_extensions(files, extensions):
    others_cnt = 0

    counter_dict = {extension: 0 for extension in extensions}
    for f_name in files:
        in_extensions = False

        for extension in extensions:
            if f_name.endswith(extension):
                in_extensions = True
                counter_dict[extension] += 1
                break

        if not in_extensions:
            if f_name.endswith("doc"):
                counter_dict['docx'] += 1
            elif f_name.endswith("xls"):
                counter_dict['xlsx'] += 1
            else:
                others_cnt += 1

    counter_dict['other'] = others_cnt
    return counter_dict


def choose_subscriptions(credentials):
    tenants_ids, tenants_names, subs_ids, subs_names = get_tenants_and_subscriptions(credentials)
    print(f"[+] Found {len(subs_ids)} subscriptions", flush=True)
    if cli_arguments.auto:
        return tenants_ids, tenants_names, subs_ids, subs_names
    response = pyip.inputMenu(['N', 'Y'],
                              "Do you wish to run the script on all the subscriptions?\n"
                              "Enter Y for all subscriptions\n"
                              "Enter N to choose for specific subscriptions\n")
    if response == 'Y':
        return tenants_ids, tenants_names, subs_ids, subs_names
    response_sub = pyip.inputMenu(subs_names, "Enter the specific subscriptions you wish to test\n")
    subs_index = subs_names.index(response_sub)
    return tenants_ids[subs_index], tenants_names[subs_index], subs_ids[subs_index], subs_names[subs_index]


def main():
    print_logo()
    credentials = get_credentials()
    delete_csv()

    if credentials is None:
        print("[-] Unable to login to a valid Azure user", flush=True)
        return

    tenants_ids, tenants_names, subs_ids, subs_names = choose_subscriptions(credentials)

    if type(tenants_ids) is list:
        for i in range(len(subs_ids)):
            check_subscription(tenants_ids[i], tenants_names[i], subs_ids[i], subs_names[i], credentials)
    else:
        check_subscription(tenants_ids, tenants_names, subs_ids, subs_names, credentials)

    print("\n[+] Scanned all subscriptions successfully", flush=True)
    print(
        f"[+] Check out {cli_arguments.output} file for a fully detailed report",
        flush=True,
    )


if __name__ == '__main__':
    main()
