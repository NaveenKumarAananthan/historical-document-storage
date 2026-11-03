# Historical Intelligence document storage

This is a separate service. It implements the existing application's HTTP storage
protocol; no Historical Intelligence application code changes are required.
It is prepared for the team's test deployment, not deployed or connected yet.

## 1. Create a separate GitHub repository

Create a private, empty repository named `historical-document-storage` in the
NaveenKumarAananthan account. Do not initialise a README, licence or .gitignore
on GitHub, because those files are already in this folder.

In PowerShell:

```powershell
cd "C:\Users\Admin\Documents\Codex\2026-10-27\no\historical-document-storage"
git init -b main
git add app.py requirements.txt Dockerfile .dockerignore .gitignore README.md test_storage.py
git commit -m "Add authenticated persistent document storage service"
git remote add origin https://github.com/NaveenKumarAananthan/historical-document-storage.git
git push -u origin main
```

If the organisation requires another repository owner, use its actual GitHub URL
instead. Do not run these commands in the Historical Intelligence repository.

## 2. Generate a shared secret on your computer

Run in PowerShell. The final output is your secret; keep it in a password manager.
Do not put it in GitHub, screenshots, chat messages or this README.

```powershell
$storageTokenBytes = New-Object byte[] 48
$storageTokenGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$storageTokenGenerator.GetBytes($storageTokenBytes)
[Convert]::ToBase64String($storageTokenBytes)
$storageTokenGenerator.Dispose()
```

## 3. Deploy the storage service on Render

This step creates a PAID service and disk. Review Render's displayed costs before
creating them. Existing application and database services are kept as they are.

1. Render Dashboard -> New -> Web Service -> connect the new private repository.
   If it is not listed, grant the Render GitHub installation access to this repo.
2. Name: `historical-document-storage` (or another available name).
3. Branch: `main`; runtime/language: Docker; Dockerfile path: `./Dockerfile`.
   Leave Root Directory blank. Leave Docker Command blank to use the Dockerfile.
4. Choose a paid instance supporting persistent disks. Prefer the same region as
   Historical Intelligence. Do not choose Free for disk-backed storage.
5. Add environment variables:

   | Key | Value |
   | --- | --- |
   | STORAGE_DIRECTORY | /var/data |
   | STORAGE_TOKEN | Your generated secret |
   | MAX_UPLOAD_BYTES | 20971520 |

6. Add a persistent disk under Advanced / Add Disk when available:
   name `source-documents`, mount path `/var/data`, size 1 GB to start (paid).
   If you must create the service first, open Disks -> Add Disk afterward and
   redeploy. A startup failure saying the directory does not exist means the disk
   has not been mounted. Do not work around it with an ordinary temporary folder.
7. Set Health Check Path to `/health` (Advanced or Settings -> Health Check).
8. Create/deploy and wait for Live. Copy its ACTUAL HTTPS service URL from Render.
   The chosen name may be adjusted by Render if already taken.
9. Open `https://YOUR-ACTUAL-BRIDGE-HOST/health`. Expect `{"status":"ok"}`.
   Opening the root `/` and seeing 404 is normal: this service has no website.

The health check proves storage is writable, not that the disk setting is durable.
Check that the persistent disk is attached and the mount path is exactly /var/data.
Only files under that mount survive redeploys. Do not remove the disk.

## 4. Connect the existing application

Render -> historical-intelligence-test -> Environment -> add or update:

| Key | Value |
| --- | --- |
| STORAGE_BACKEND | http |
| STORAGE_URL | The actual bridge HTTPS origin; no /health or /objects suffix |
| STORAGE_TOKEN | EXACTLY the same secret used by the bridge |

Use Save and deploy and wait for Live. The current implementation checks the
configured URL to enable the upload control; a working upload/download is still
required to prove authentication and storage connectivity.

Do not replace the database URL, development login settings or other existing
variables. Do not set the main application's storage backend to local. No main
application commit or repush is necessary for these environment changes.

## 5. Validate the complete workflow

1. Sign in as an account with document upload permission and archive access.
2. Open Source documents and refresh. Confirm the storage warning disappears.
3. Wait until archives finish loading; choose an active permitted archive.
4. Upload a small allowed PDF or image and enter required metadata.
5. Confirm successful upload, correct uploaded-by/date and document row.
6. Open or download the original and compare it with the uploaded file.
7. Restart/redeploy ONLY the storage bridge after all uploads finish. Wait for
   Live, then open/download the same original again. It must still be available.
8. Check Read Only cannot upload and required-field/file-type validation works.

OCR and AI processing need a separate processing integration. Storing the file
does not start or complete OCR automatically.

## Troubleshooting

- Warning still visible: confirm the variables were added to the existing
  application service, then successfully deployed; refresh the browser.
- Upload disabled after warning disappears: inspect role permission and active
  archive availability. The storage service cannot grant these permissions.
- Configured service unavailable: check bridge Live status and Logs, HTTPS URL,
  shared token match, and its /health result.
- /health fails: check disk mount, capacity and filesystem write errors in Logs.
- Original missing after restart: confirm /var/data is an attached persistent
  disk, not an ordinary directory. Do not delete/recreate the disk to troubleshoot.
- Existing imported document still says no original: upload a new document or use
  Attach original source file on its detail view where available. Configuring
  storage does not reconstruct file bytes missing from historical JSON imports.

## Service contract and maintenance

- PUT /objects/{percent-encoded-key}: Bearer authentication, create-only, 201;
  existing object 409. Writes streamed to a temporary file, then published
  atomically. Default 20 MiB, enforced even for chunked requests.
- GET: Bearer authentication, original bytes as attachment, missing object 404.
- DELETE: Bearer authentication, idempotent 204, for failed-upload cleanup.
- Keys hashed as opaque UTF-8 values, never used as filesystem paths.
- Documentation endpoints disabled; only /health is unauthenticated.
- One shared token for the application; rotate it in both services together.
- Disk files intentionally have hashed names. Use application metadata to locate
  documents; do not rename disk files manually.
- Keep backups of BOTH the app database and stored files. A persistent disk is
  not a substitute for a restore-tested backup process.

## Local verification

Python 3.12 is used in Docker. Install requirements plus httpx for local tests:

```powershell
python -m pip install -r requirements.txt httpx
python -m unittest -v test_storage
```

Tests cover authentication, exact binary round trips with encoded keys, immutable
originals, concurrent create protection, oversize/streamed cleanup, opaque keys,
restart persistence, deletion, bad configuration, missing mount, and real HTTP
upload/download/delete using the application's URL encoding pattern.

## Render references

- https://render.com/docs/disks
- https://render.com/docs/configure-environment-variables
- https://render.com/docs/docker
