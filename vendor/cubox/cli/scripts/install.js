const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const os = require("os");

const VERSION = require("../package.json").version;
const REPO = "OLCUBO/cubox-cli";
const NAME = "cubox-cli";

// SHA-256 of the official release assets. Source: GitHub release asset
// digests for OLCUBO/cubox-cli v1.0.10 (gh release view v1.0.10), re-checked
// against freshly downloaded files on 2026-11-02. A version or archive that is
// not listed here is refused instead of installed unverified.
const SHA256 = {
  "cubox-cli-1.0.10-darwin-amd64.tar.gz": "6808c88ce346297d8a56f17c2ff566e43263f138342788ab2368aae393227918",
  "cubox-cli-1.0.10-darwin-arm64.tar.gz": "59ebcdf50f1fe03bbad808ee1d565bde449eafab64dd39dd037884819343ec2f",
  "cubox-cli-1.0.10-linux-amd64.tar.gz": "01ce2d69259ff5b95a036841d5ed1c4cf9bcc4079b5f47d2bdd22f9bb28b8f17",
  "cubox-cli-1.0.10-linux-arm64.tar.gz": "99ac46075556c8a572dac03353a486a7da8302ec188b0fe8dee39fcde6677747",
  "cubox-cli-1.0.10-windows-amd64.zip": "acc46abea971f824f346f2bcfdd06a1a3565e4f396b2ce5c89a1692c99e1be94",
  "cubox-cli-1.0.10-windows-arm64.zip": "c2355b42bcce72d8effc7aefbf5b32c204c072f2a4fb39076fd602268240a3c2",
};

function verifyArchive(filePath, name) {
  const expected = SHA256[name];
  if (!expected) {
    throw new Error(`No pinned SHA-256 for ${name}; refusing to install an unverified binary`);
  }
  const actual = crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
  if (actual !== expected) {
    throw new Error(`SHA-256 mismatch for ${name}: expected ${expected}, got ${actual}`);
  }
}

const PLATFORM_MAP = {
  darwin: "darwin",
  linux: "linux",
  win32: "windows",
};

const ARCH_MAP = {
  x64: "amd64",
  arm64: "arm64",
};

module.exports = { SHA256, verifyArchive };
if (require.main !== module) {
  return;
}

const platform = PLATFORM_MAP[process.platform];
const arch = ARCH_MAP[process.arch];

if (!platform || !arch) {
  console.error(
    `Unsupported platform: ${process.platform}-${process.arch}`
  );
  process.exit(1);
}

const isWindows = process.platform === "win32";
const ext = isWindows ? ".zip" : ".tar.gz";
const archiveName = `${NAME}-${VERSION}-${platform}-${arch}${ext}`;
const GITHUB_URL = `https://github.com/${REPO}/releases/download/v${VERSION}/${archiveName}`;

const binDir = path.join(__dirname, "..", "bin");
const dest = path.join(binDir, NAME + (isWindows ? ".exe" : ""));

fs.mkdirSync(binDir, { recursive: true });

function download(url, destPath) {
  const sslFlag = isWindows ? "--ssl-revoke-best-effort " : "";
  execSync(
    `curl ${sslFlag}--fail --location --silent --show-error --connect-timeout 10 --max-time 120 --output "${destPath}" "${url}"`,
    { stdio: ["ignore", "ignore", "pipe"] }
  );
}

function install() {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "cubox-cli-"));
  const archivePath = path.join(tmpDir, archiveName);

  try {
    download(GITHUB_URL, archivePath);
    verifyArchive(archivePath, archiveName);

    if (isWindows) {
      execSync(
        `powershell -Command "Expand-Archive -Path '${archivePath}' -DestinationPath '${tmpDir}'"`,
        { stdio: "ignore" }
      );
    } else {
      execSync(`tar -xzf "${archivePath}" -C "${tmpDir}"`, {
        stdio: "ignore",
      });
    }

    const binaryName = NAME + (isWindows ? ".exe" : "");
    const extractedBinary = path.join(tmpDir, binaryName);

    fs.copyFileSync(extractedBinary, dest);
    fs.chmodSync(dest, 0o755);
    console.log(`${NAME} v${VERSION} installed successfully`);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

try {
  install();
} catch (err) {
  console.error(`Failed to install ${NAME}:`, err.message);
  console.error(
    `\nYou can also build from source:\n` +
    `  git clone https://github.com/${REPO}.git\n` +
    `  cd cubox-cli\n` +
    `  make install\n` +
    `\nIf behind a firewall, try setting a proxy:\n` +
    `  export https_proxy=http://your-proxy:port\n` +
    `  npm install -g cubox-cli`
  );
  process.exit(1);
}
