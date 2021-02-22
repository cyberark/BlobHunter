import azure.core.exceptions
from datetime import date
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import SubscriptionClient, ResourceManagementClient
from azure.mgmt.storage import StorageManagementClient
from azure.storage.blob import BlobServiceClient, ContainerClient
import subprocess
import csv
import os

ENDPOINT_URL = '{}.blob.core.windows.net'
CONTAINER_URL = '{}.blob.core.windows.net/{}/'
EXTENSIONS = ["txt", "csv", "pdf", "docx", "xlsx"]



def get_tenants_and_subscriptions(creds):
    subscription_client = SubscriptionClient(creds)
    tenants_ids = list()
    tenants_names = list()
    subscriptions_ids = list()
    subscription_names = list()

    for sub in subscription_client.subscriptions.list():
        tenants_ids.append(sub.tenant_id)
        subscriptions_ids.append(sub.id[15:])
        subscription_names.append(sub.display_name)

    # Getting tenant name from given tenant id
    for ten_id in tenants_ids:
        for ten in subscription_client.tenants.list():
            if ten_id == ten.id[9:]:
                tenants_names.append(ten.display_name)

    return tenants_ids, tenants_names, subscriptions_ids, subscription_names


def check_storage_account(account_name, key):
    blob_service_client = BlobServiceClient(ENDPOINT_URL.format(account_name), credential=key)
    containers = blob_service_client.list_containers(timeout=15)

    public_containers = list()
    for cont in containers:
        if cont.public_access is not None:
            public_containers.append(cont)

    return public_containers


def check_subscription(tenant_id, tenant_name, sub_id, sub_name, creds):
    print("\n\t[*] Checking subscription {}:".format(sub_name), flush=True)

    storage_client = StorageManagementClient(creds, sub_id)

    # Obtain the management object for resources
    resource_client = ResourceManagementClient(creds, sub_id)

    # Retrieve the list of resource groups
    group_list = resource_client.resource_groups.list()
    resource_groups = [group.name for group in list(group_list)]
    print("\t\t[+] Found {} resource groups".format(len(resource_groups)), flush=True)
    group_to_names_dict = {group: dict() for group in resource_groups}

    accounts_counter = 0
    for group in resource_groups:
        for item in storage_client.storage_accounts.list_by_resource_group(group):
            accounts_counter += 1
            group_to_names_dict[group][item.name] = ''

    print("\t\t[+] Found {} storage accounts".format(accounts_counter), flush=True)

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

    output_list = list()

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

                for ext in ext_dict.keys():
                    row.append(ext_dict[ext])

                output_list.append(row)

    print("\t\t[+] Scanned all storage accounts successfully", flush=True)

    if len(output_list) > 0:
        print("\t\t[+] Found {} PUBLIC containers".format(len(output_list)), flush=True)
    else:
        print("\t\t[+] No PUBLIC containers found")

    header = ["Tenant ID", "Tenant Name", "Subscription ID", "Subscription Name", "Resource Group", "Storage Account", "Container",
              "Public Access Level", "URL", "Total Files"]

    for ext in EXTENSIONS:
        header.append(ext)

    header.append("others")
    write_csv('public-containers-{}.csv'.format(date.today()), header, output_list)


def delete_csv():
    for file in os.listdir("."):
        if os.path.isfile(file) and file.startswith("public"):
            os.remove(file)


def write_csv(file_name, header, rows):
    file_exists = os.path.isfile(file_name)

    with open(file_name, 'a', newline='', encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)

        if not file_exists:
            writer.writerow(header)

        for r in rows:
            writer.writerow(r)


def count_files_extensions(files, extensions):
    counter_dict = dict()
    others_cnt = 0

    for extension in extensions:
        counter_dict[extension] = 0

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


def print_logo():
    logo = '''
-------------------------------------------------------------    
    
    ______ _       _     _   _             _            
    | ___ \ |     | |   | | | |           | |           
    | |_/ / | ___ | |__ | |_| |_   _ _ __ | |_ ___ _ __ 
    | ___ \ |/ _ \| '_ \|  _  | | | | '_ \| __/ _ \ '__|
    | |_/ / | (_) | |_) | | | | |_| | | | | ||  __/ |   
    \____/|_|\___/|_.__/\_| |_/\__,_|_| |_|\__\___|_|
                                                                  
-------------------------------------------------------------  
                    Author: Daniel Niv
------------------------------------------------------------- 
                                       
    '''

    print(logo, flush=True)


def main():
    print_logo()
    credentials = DefaultAzureCredentials()
    delete_csv()

    if credentials is None:
        print("[-] Unable to login to a valid Azure user", flush=True)
        return

    tenants_ids, tenants_names, subs_ids, subs_names = get_tenants_and_subscriptions(credentials)
    print("[+] Found {} subscriptions".format(len(subs_ids)), flush=True)

    for i in range(0, len(subs_ids)):
        check_subscription(tenants_ids[i], tenants_names[i], subs_ids[i], subs_names[i], credentials)

    print("\n[+] Scanned all subscriptions successfully", flush=True)
    print("[+] Check out public-containers-{}.csv file for a fully detailed report".format(date.today()), flush=True)


if __name__ == '__main__':
    main()
