"""内嵌的 ed25519 主机密钥(仅当 bin/ssh-keygen 不可用时兜底)。

由 fixssh.py 生成;私钥只在容器内部使用,不对外分发。
"""
PUB_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMRXALoIXpB/MVqoVQ/M97M9cB9SPCK2eV9867boHHau "
    "mosbox-host-node10"
)

_PRIV_B64 = (
    "LS0tLS1CRUdJTiBPUEVOU1NIIFBSSVZBVEUgS0VZLS0tLS0KYjNCbGJuTnphQzFyWlhrdGRqRUFB"
    "QUFBQkc1dmJtVUFBQUFFYm05dVpRQUFBQUFBQUFBQkFBQUFNd0FBQUF0emMyZ3RaVwpReU5UVXhP"
    "UUFBQUNERVZ3QzZDRjZRZnpGYXFGVVB6UGV6UFhBZlVqd2l0bmxmZk91MjZCeDJyZ0FBQUppMk1I"
    "OXV0akIvCmJnQUFBQXR6YzJndFpXUXlOVFV4T1FBQUFDREVWd0M2Q0Y2UWZ6RmFxRlVQelBlelBY"
    "QWZVandpdG5sZmZPdTI2QngycmcKQUFBRUI3MkptdmFmd25LRU1mc1o2RSt2RllzNkdRemRLdENn"
    "N3N3U2R6RFJuRHo4UlhBTG9JWHBCL01WcW9WUS9NOTdNOQpjQjlTUENLMmVWOTg2N2JvSEhhdUFB"
    "QUFFbTF2YzJKdmVDMW9iM04wTFc1dlpHVXhNQUVDQXc9PQotLS0tLUVORCBPUEVOU1NIIFBSSVZB"
    "VEUgS0VZLS0tLS0K"
)


def write_host_key(dest) -> bool:
    """把内嵌私钥写到 dest,返回是否成功。"""
    import base64 as _b64
    from pathlib import Path
    p = Path(dest)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_b64.b64decode(_PRIV_B64))
        p.chmod(0o600)
        Path(str(p) + ".pub").write_text(PUB_KEY + "\n", encoding="utf-8")
        return True
    except OSError:
        return False
