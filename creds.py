# import os
# from google.cloud import secretmanager

# print("Collecting credentials")
# # Access credentials from Secret Manager
# client = secretmanager.SecretManagerServiceClient()
# project_id = "shining-berm-396016"
# secret_name = "keyfile"
# version_id = "latest"
# name = f"projects/{project_id}/secrets/{secret_name}/versions/{version_id}"
# response = client.access_secret_version(request={"name": name})

# # Set the credentials as environment variables
# credentials_json = response.payload.data.decode("UTF-8")
# os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_json