---
**Source:** https://github.com/sqlcipher/sqlcipher/blob/master/src/sqlcipher.c (lines 2573–3205)

**Fetched:** 2026-04-22

**License:** BSD-style (sqlcipher/LICENSE.md, (c) 2025 ZETETIC LLC)

**Note:** Excerpted verbatim from sqlcipher.c — this is the C source that implements all SQLCipher-specific PRAGMAs: `key`, `rekey`, `cipher_migrate`, `cipher_version`, `cipher_provider`, `cipher_page_size`, `cipher_kdf_iter`, `cipher_kdf_algorithm`, `cipher_hmac_algorithm`, `cipher_salt`, `cipher_plaintext_header_size`, `cipher_default_*` and others. Read the if/else-if chain as a directory of supported PRAGMAs.
---

# SQLCipher PRAGMA handler — source excerpt

```c
int sqlcipher_codec_pragma(sqlite3* db, int iDb, Parse *pParse, const char *zLeft, const char *zRight) {
  struct Db *pDb = &db->aDb[iDb];
  codec_ctx *ctx = NULL;
  int rc;

  if(pDb->pBt) {
    ctx = (codec_ctx*) sqlcipherPagerGetCodec(sqlite3BtreePager(pDb->pBt));
  }

  if(sqlite3_stricmp(zLeft, "key") !=0 && sqlite3_stricmp(zLeft, "rekey") != 0) {
    sqlcipher_log(SQLCIPHER_LOG_DEBUG, SQLCIPHER_LOG_CORE, "sqlcipher_codec_pragma: db=%p iDb=%d pParse=%p zLeft=%s zRight=%s ctx=%p", db, iDb, pParse, zLeft, zRight, ctx);
  }

#ifdef SQLCIPHER_TEST
  if( sqlite3_stricmp(zLeft,"cipher_test_on")==0 ){
    if( zRight ) {
      if(sqlite3_stricmp(zRight, "fail_encrypt")==0) {
        SQLCIPHER_FLAG_SET(cipher_test_flags,TEST_FAIL_ENCRYPT);
      } else
      if(sqlite3_stricmp(zRight, "fail_decrypt")==0) {
        SQLCIPHER_FLAG_SET(cipher_test_flags,TEST_FAIL_DECRYPT);
      } else
      if(sqlite3_stricmp(zRight, "fail_migrate")==0) {
        SQLCIPHER_FLAG_SET(cipher_test_flags,TEST_FAIL_MIGRATE);
      }
    }
  } else
  if( sqlite3_stricmp(zLeft,"cipher_test_off")==0 ){
    if( zRight ) {
      if(sqlite3_stricmp(zRight, "fail_encrypt")==0) {
        SQLCIPHER_FLAG_UNSET(cipher_test_flags,TEST_FAIL_ENCRYPT);
      } else
      if(sqlite3_stricmp(zRight, "fail_decrypt")==0) {
        SQLCIPHER_FLAG_UNSET(cipher_test_flags,TEST_FAIL_DECRYPT);
      } else
      if(sqlite3_stricmp(zRight, "fail_migrate")==0) {
        SQLCIPHER_FLAG_UNSET(cipher_test_flags,TEST_FAIL_MIGRATE);
      }
    }
  } else
  if( sqlite3_stricmp(zLeft,"cipher_test")==0 ){
    char *flags = sqlite3_mprintf("%u", cipher_test_flags);
    sqlcipher_vdbe_return_string(pParse, "cipher_test", flags, P4_DYNAMIC);
  }else
  if( sqlite3_stricmp(zLeft,"cipher_test_rand")==0 ){
    if( zRight ) {
      int rand = atoi(zRight);
      cipher_test_rand = rand;
    } else {
      char *rand = sqlite3_mprintf("%d", cipher_test_rand);
      sqlcipher_vdbe_return_string(pParse, "cipher_test_rand", rand, P4_DYNAMIC);
    }
  } else
#endif
  if( sqlite3_stricmp(zLeft, "cipher_fips_status")== 0 && !zRight ){
    if(ctx) {
      char *fips_mode_status = sqlite3_mprintf("%d", ctx->provider->fips_status(ctx->provider_ctx));
      sqlcipher_vdbe_return_string(pParse, "cipher_fips_status", fips_mode_status, P4_DYNAMIC);
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_status")== 0 && !zRight ){
    if(ctx && ctx->error == SQLITE_OK) {
      sqlcipher_vdbe_return_string(pParse, "cipher_status", "1", P4_TRANSIENT);
    } else {
      sqlcipher_vdbe_return_string(pParse, "cipher_status", "0", P4_TRANSIENT);
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_store_pass")==0 && zRight ) {
    if(ctx) {
      char *deprecation = "PRAGMA cipher_store_pass is deprecated, please remove from use";
      ctx->store_pass = sqlite3GetBoolean(zRight, 1);
      sqlcipher_vdbe_return_string(pParse, "cipher_store_pass", deprecation, P4_TRANSIENT);
      sqlite3_log(SQLITE_WARNING, deprecation);
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_store_pass")==0 && !zRight ) {
    if(ctx){
      char *store_pass_value = sqlite3_mprintf("%d", ctx->store_pass);
      sqlcipher_vdbe_return_string(pParse, "cipher_store_pass", store_pass_value, P4_DYNAMIC);
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_profile")== 0 && zRight ){
      char *profile_status = sqlite3_mprintf("%d", sqlcipher_cipher_profile(db, zRight));
      sqlcipher_vdbe_return_string(pParse, "cipher_profile", profile_status, P4_DYNAMIC);
  } else
  if( sqlite3_stricmp(zLeft, "cipher_add_random")==0 && zRight ){
    if(ctx) {
      char *add_random_status = sqlite3_mprintf("%d", sqlcipher_codec_add_random(ctx, zRight, sqlite3Strlen30(zRight)));
      sqlcipher_vdbe_return_string(pParse, "cipher_add_random", add_random_status, P4_DYNAMIC);
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_migrate")==0 && !zRight ){
    if(ctx){
      int status = sqlcipher_codec_ctx_migrate(ctx); 
      char *migrate_status = sqlite3_mprintf("%d", status);
      sqlcipher_vdbe_return_string(pParse, "cipher_migrate", migrate_status, P4_DYNAMIC);
      if(status != SQLITE_OK) {
        sqlcipher_log(SQLCIPHER_LOG_ERROR, SQLCIPHER_LOG_CORE, "sqlcipher_codec_pragma: error occurred during cipher_migrate: %d", status);
      }
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_provider")==0 && !zRight ){
    if(ctx) {
      sqlcipher_vdbe_return_string(pParse, "cipher_provider",
        ctx->provider->get_provider_name(ctx->provider_ctx), P4_TRANSIENT);
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_provider_version")==0 && !zRight){
    if(ctx) {
      sqlcipher_vdbe_return_string(pParse, "cipher_provider_version",
        ctx->provider->get_provider_version(ctx->provider_ctx), P4_TRANSIENT);
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_version")==0 && !zRight ){
    sqlcipher_vdbe_return_string(pParse, "cipher_version", sqlcipher_version(), P4_DYNAMIC);
  }else
  if( sqlite3_stricmp(zLeft, "cipher")==0 ){
    if(ctx) {
      if( zRight ) {
        const char* message = "PRAGMA cipher is no longer supported.";
        sqlcipher_vdbe_return_string(pParse, "cipher", message, P4_TRANSIENT);
        sqlite3_log(SQLITE_WARNING, message);
      }else {
        sqlcipher_vdbe_return_string(pParse, "cipher", 
          ctx->provider->get_cipher(ctx->provider_ctx), P4_TRANSIENT); 
      }
    }
  }else
  if( sqlite3_stricmp(zLeft, "rekey_cipher")==0 && zRight ){
    const char* message = "PRAGMA rekey_cipher is no longer supported.";
    sqlcipher_vdbe_return_string(pParse, "rekey_cipher", message, P4_TRANSIENT);
    sqlite3_log(SQLITE_WARNING, message);
  }else
  if( sqlite3_stricmp(zLeft,"cipher_default_kdf_iter")==0 ){
    if( zRight ) {
      default_kdf_iter = atoi(zRight); /* change default KDF iterations */
    } else {
      char *kdf_iter = sqlite3_mprintf("%d", default_kdf_iter);
      sqlcipher_vdbe_return_string(pParse, "cipher_default_kdf_iter", kdf_iter, P4_DYNAMIC);
    }
  }else
  if( sqlite3_stricmp(zLeft, "kdf_iter")==0 ){
    if(ctx) {
      if( zRight ) {
        sqlcipher_codec_ctx_set_kdf_iter(ctx, atoi(zRight)); /* change of RW PBKDF2 iteration */
      } else {
        char *kdf_iter = sqlite3_mprintf("%d", ctx->kdf_iter);
        sqlcipher_vdbe_return_string(pParse, "kdf_iter", kdf_iter, P4_DYNAMIC);
      }
    }
  }else
  if( sqlite3_stricmp(zLeft, "fast_kdf_iter")==0){
    if(ctx) {
      if( zRight ) {
        char *deprecation = "PRAGMA fast_kdf_iter is deprecated, please remove from use";
        sqlcipher_codec_ctx_set_fast_kdf_iter(ctx, atoi(zRight)); /* change of RW PBKDF2 iteration */
        sqlcipher_vdbe_return_string(pParse, "fast_kdf_iter", deprecation, P4_TRANSIENT);
        sqlite3_log(SQLITE_WARNING, deprecation);
      } else {
        char *fast_kdf_iter = sqlite3_mprintf("%d", ctx->fast_kdf_iter);
        sqlcipher_vdbe_return_string(pParse, "fast_kdf_iter", fast_kdf_iter, P4_DYNAMIC);
      }
    }
  }else
  if( sqlite3_stricmp(zLeft, "rekey_kdf_iter")==0 && zRight ){
    const char* message = "PRAGMA rekey_kdf_iter is no longer supported.";
    sqlcipher_vdbe_return_string(pParse, "rekey_kdf_iter", message, P4_TRANSIENT);
    sqlite3_log(SQLITE_WARNING, message);
  }else
  if( sqlite3_stricmp(zLeft,"page_size")==0 || sqlite3_stricmp(zLeft,"cipher_page_size")==0 ){
    /* PRAGMA cipher_page_size will alter the size of the database pages while ensuring that the
       required reserve space is allocated at the end of each page. This will also override the
       standard SQLite PRAGMA page_size behavior if a codec context is attached to the database handle.
       If PRAGMA page_size is invoked but a codec context is not attached (i.e. dealing with a standard
       unencrypted database) then return early and allow the standard PRAGMA page_size logic to apply. */
    if(ctx) {
      if( zRight ) {
        int size = atoi(zRight);
        rc = sqlcipher_codec_ctx_set_pagesize(ctx, size);
        if(rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, rc);
        rc = codec_set_btree_to_codec_pagesize(db, pDb, ctx);
        if(rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, rc);
      } else {
        char * page_size = sqlite3_mprintf("%d", ctx->page_sz);
        sqlcipher_vdbe_return_string(pParse, "cipher_page_size", page_size, P4_DYNAMIC);
      }
    } else {
      return 0; /* return early so that the PragTyp_PAGE_SIZE case logic in pragma.c will take effect */
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_default_page_size")==0 ){
    if( zRight ) {
      default_page_size = atoi(zRight);
    } else {
      char *page_size = sqlite3_mprintf("%d", default_page_size);
      sqlcipher_vdbe_return_string(pParse, "cipher_default_page_size", page_size, P4_DYNAMIC);
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_default_use_hmac")==0 ){
    if( zRight ) {
      sqlcipher_set_default_use_hmac(sqlite3GetBoolean(zRight,1));
    } else {
      char *default_use_hmac = sqlite3_mprintf("%d", SQLCIPHER_FLAG_GET(default_flags, CIPHER_FLAG_HMAC));
      sqlcipher_vdbe_return_string(pParse, "cipher_default_use_hmac", default_use_hmac, P4_DYNAMIC);
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_use_hmac")==0 ){
    if(ctx) {
      if( zRight ) {
        rc = sqlcipher_codec_ctx_set_use_hmac(ctx, sqlite3GetBoolean(zRight,1));
        if(rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, rc);
        /* since the use of hmac has changed, the page size may also change */
        rc = codec_set_btree_to_codec_pagesize(db, pDb, ctx);
        if(rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, rc);
      } else {
        char *hmac_flag = sqlite3_mprintf("%d", SQLCIPHER_FLAG_GET(ctx->flags, CIPHER_FLAG_HMAC));
        sqlcipher_vdbe_return_string(pParse, "cipher_use_hmac", hmac_flag, P4_DYNAMIC);
      }
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_hmac_pgno")==0 ){
    if(ctx) {
      if(zRight) {
        char *deprecation = "PRAGMA cipher_hmac_pgno is deprecated, please remove from use";
        /* clear both pgno endian flags */
        if(sqlite3_stricmp(zRight, "le") == 0) {
          SQLCIPHER_FLAG_UNSET(ctx->flags, CIPHER_FLAG_BE_PGNO);
          SQLCIPHER_FLAG_SET(ctx->flags, CIPHER_FLAG_LE_PGNO);
        } else if(sqlite3_stricmp(zRight, "be") == 0) {
          SQLCIPHER_FLAG_UNSET(ctx->flags, CIPHER_FLAG_LE_PGNO);
          SQLCIPHER_FLAG_SET(ctx->flags, CIPHER_FLAG_BE_PGNO);
        } else if(sqlite3_stricmp(zRight, "native") == 0) {
          SQLCIPHER_FLAG_UNSET(ctx->flags, CIPHER_FLAG_LE_PGNO);
          SQLCIPHER_FLAG_UNSET(ctx->flags, CIPHER_FLAG_BE_PGNO);
        }
        sqlcipher_vdbe_return_string(pParse, "cipher_hmac_pgno", deprecation, P4_TRANSIENT);
        sqlite3_log(SQLITE_WARNING, deprecation);
 
      } else {
        if(SQLCIPHER_FLAG_GET(ctx->flags, CIPHER_FLAG_LE_PGNO)) {
          sqlcipher_vdbe_return_string(pParse, "cipher_hmac_pgno", "le", P4_TRANSIENT);
        } else if(SQLCIPHER_FLAG_GET(ctx->flags, CIPHER_FLAG_BE_PGNO)) {
          sqlcipher_vdbe_return_string(pParse, "cipher_hmac_pgno", "be", P4_TRANSIENT);
        } else {
          sqlcipher_vdbe_return_string(pParse, "cipher_hmac_pgno", "native", P4_TRANSIENT);
        }
      }
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_hmac_salt_mask")==0 ){
    if(ctx) {
      if(zRight) {
        char *deprecation = "PRAGMA cipher_hmac_salt_mask is deprecated, please remove from use";
        if (sqlite3StrNICmp(zRight ,"x'", 2) == 0 && sqlite3Strlen30(zRight) == 5) {
          unsigned char mask = 0;
          const unsigned char *hex = (const unsigned char *)zRight+2;
          cipher_hex2bin(hex,2,&mask);
          hmac_salt_mask = mask;
        }
        sqlcipher_vdbe_return_string(pParse, "cipher_hmac_salt_mask", deprecation, P4_TRANSIENT);
        sqlite3_log(SQLITE_WARNING, deprecation);
      } else {
        char *mask = sqlite3_mprintf("%02x", hmac_salt_mask);
        sqlcipher_vdbe_return_string(pParse, "cipher_hmac_salt_mask", mask, P4_DYNAMIC);
      }
    }
  }else 
  if( sqlite3_stricmp(zLeft,"cipher_plaintext_header_size")==0 ){
    if(ctx) {
      if( zRight ) {
        int size = atoi(zRight);
        /* deliberately ignore result code, if size is invalid it will be set to -1
           and trip the error later in the codec */
        sqlcipher_codec_ctx_set_plaintext_header_size(ctx, size);
      } else {
        char *size = sqlite3_mprintf("%d", ctx->plaintext_header_sz);
        sqlcipher_vdbe_return_string(pParse, "cipher_plaintext_header_size", size, P4_DYNAMIC);
      }
    }
  }else 
  if( sqlite3_stricmp(zLeft,"cipher_default_plaintext_header_size")==0 ){
    if( zRight ) {
      default_plaintext_header_size = atoi(zRight);
    } else {
      char *size = sqlite3_mprintf("%d", default_plaintext_header_size);
      sqlcipher_vdbe_return_string(pParse, "cipher_default_plaintext_header_size", size, P4_DYNAMIC);
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_salt")==0 ){
    if(ctx) {
      if(zRight) {
        if (sqlite3StrNICmp(zRight ,"x'", 2) == 0 && sqlite3Strlen30(zRight) == (FILE_HEADER_SZ*2)+3) {
          unsigned char *salt = (unsigned char*) sqlite3_malloc(FILE_HEADER_SZ);
          const unsigned char *hex = (const unsigned char *)zRight+2;
          cipher_hex2bin(hex,FILE_HEADER_SZ*2,salt);
          sqlcipher_codec_ctx_set_kdf_salt(ctx, salt, FILE_HEADER_SZ);
          sqlite3_free(salt);
        }
      } else {
        void *salt;
        char *hexsalt = (char*) sqlite3_malloc((FILE_HEADER_SZ*2)+1);
        if((rc = sqlcipher_codec_ctx_get_kdf_salt(ctx, &salt)) == SQLITE_OK) {
          cipher_bin2hex(salt, FILE_HEADER_SZ, hexsalt);
          sqlcipher_vdbe_return_string(pParse, "cipher_salt", hexsalt, P4_DYNAMIC);
        } else {
          sqlite3_free(hexsalt);
          sqlcipher_codec_ctx_set_error(ctx, rc);
        }
      }
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_hmac_algorithm")==0 ){
    if(ctx) {
      if(zRight) {
        rc = SQLITE_ERROR;
        if(sqlite3_stricmp(zRight, SQLCIPHER_HMAC_SHA1_LABEL) == 0) {
          rc = sqlcipher_codec_ctx_set_hmac_algorithm(ctx, SQLCIPHER_HMAC_SHA1);
        } else if(sqlite3_stricmp(zRight, SQLCIPHER_HMAC_SHA256_LABEL) == 0) {
          rc = sqlcipher_codec_ctx_set_hmac_algorithm(ctx, SQLCIPHER_HMAC_SHA256);
        } else if(sqlite3_stricmp(zRight, SQLCIPHER_HMAC_SHA512_LABEL) == 0) {
          rc = sqlcipher_codec_ctx_set_hmac_algorithm(ctx, SQLCIPHER_HMAC_SHA512);
        }
        if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
        rc = codec_set_btree_to_codec_pagesize(db, pDb, ctx);
        if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
      } else {
        int algorithm = ctx->hmac_algorithm;
        if(ctx->hmac_algorithm == SQLCIPHER_HMAC_SHA1) {
          sqlcipher_vdbe_return_string(pParse, "cipher_hmac_algorithm", SQLCIPHER_HMAC_SHA1_LABEL, P4_TRANSIENT);
        } else if(algorithm == SQLCIPHER_HMAC_SHA256) {
          sqlcipher_vdbe_return_string(pParse, "cipher_hmac_algorithm", SQLCIPHER_HMAC_SHA256_LABEL, P4_TRANSIENT);
        } else if(algorithm == SQLCIPHER_HMAC_SHA512) {
          sqlcipher_vdbe_return_string(pParse, "cipher_hmac_algorithm", SQLCIPHER_HMAC_SHA512_LABEL, P4_TRANSIENT);
        }
      }
    }
  }else 
  if( sqlite3_stricmp(zLeft,"cipher_default_hmac_algorithm")==0 ){
    if(zRight) {
      rc = SQLITE_OK;
      if(sqlite3_stricmp(zRight, SQLCIPHER_HMAC_SHA1_LABEL) == 0) {
        default_hmac_algorithm = SQLCIPHER_HMAC_SHA1;
      } else if(sqlite3_stricmp(zRight, SQLCIPHER_HMAC_SHA256_LABEL) == 0) {
        default_hmac_algorithm = SQLCIPHER_HMAC_SHA256;
      } else if(sqlite3_stricmp(zRight, SQLCIPHER_HMAC_SHA512_LABEL) == 0) {
        default_hmac_algorithm = SQLCIPHER_HMAC_SHA512;
      }
    } else {
      if(default_hmac_algorithm == SQLCIPHER_HMAC_SHA1) {
        sqlcipher_vdbe_return_string(pParse, "cipher_default_hmac_algorithm", SQLCIPHER_HMAC_SHA1_LABEL, P4_TRANSIENT);
      } else if(default_hmac_algorithm == SQLCIPHER_HMAC_SHA256) {
        sqlcipher_vdbe_return_string(pParse, "cipher_default_hmac_algorithm", SQLCIPHER_HMAC_SHA256_LABEL, P4_TRANSIENT);
      } else if(default_hmac_algorithm == SQLCIPHER_HMAC_SHA512) {
        sqlcipher_vdbe_return_string(pParse, "cipher_default_hmac_algorithm", SQLCIPHER_HMAC_SHA512_LABEL, P4_TRANSIENT);
      }
    }
  }else 
  if( sqlite3_stricmp(zLeft,"cipher_kdf_algorithm")==0 ){
    if(ctx) {
      if(zRight) {
        rc = SQLITE_ERROR;
        if(sqlite3_stricmp(zRight, SQLCIPHER_PBKDF2_HMAC_SHA1_LABEL) == 0) {
          rc = sqlcipher_codec_ctx_set_kdf_algorithm(ctx, SQLCIPHER_PBKDF2_HMAC_SHA1);
        } else if(sqlite3_stricmp(zRight, SQLCIPHER_PBKDF2_HMAC_SHA256_LABEL) == 0) {
          rc = sqlcipher_codec_ctx_set_kdf_algorithm(ctx, SQLCIPHER_PBKDF2_HMAC_SHA256);
        } else if(sqlite3_stricmp(zRight, SQLCIPHER_PBKDF2_HMAC_SHA512_LABEL) == 0) {
          rc = sqlcipher_codec_ctx_set_kdf_algorithm(ctx, SQLCIPHER_PBKDF2_HMAC_SHA512);
        }
        if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
      } else {
        if(ctx->kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA1) {
          sqlcipher_vdbe_return_string(pParse, "cipher_kdf_algorithm", SQLCIPHER_PBKDF2_HMAC_SHA1_LABEL, P4_TRANSIENT);
        } else if(ctx->kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA256) {
          sqlcipher_vdbe_return_string(pParse, "cipher_kdf_algorithm", SQLCIPHER_PBKDF2_HMAC_SHA256_LABEL, P4_TRANSIENT);
        } else if(ctx->kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA512) {
          sqlcipher_vdbe_return_string(pParse, "cipher_kdf_algorithm", SQLCIPHER_PBKDF2_HMAC_SHA512_LABEL, P4_TRANSIENT);
        }
      }
    }
  }else 
  if( sqlite3_stricmp(zLeft,"cipher_default_kdf_algorithm")==0 ){
    if(zRight) {
      rc = SQLITE_OK;
      if(sqlite3_stricmp(zRight, SQLCIPHER_PBKDF2_HMAC_SHA1_LABEL) == 0) {
        default_kdf_algorithm = SQLCIPHER_PBKDF2_HMAC_SHA1;
      } else if(sqlite3_stricmp(zRight, SQLCIPHER_PBKDF2_HMAC_SHA256_LABEL) == 0) {
        default_kdf_algorithm = SQLCIPHER_PBKDF2_HMAC_SHA256;
      } else if(sqlite3_stricmp(zRight, SQLCIPHER_PBKDF2_HMAC_SHA512_LABEL) == 0) {
        default_kdf_algorithm = SQLCIPHER_PBKDF2_HMAC_SHA512;
      }
    } else {
      if(default_kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA1) {
        sqlcipher_vdbe_return_string(pParse, "cipher_default_kdf_algorithm", SQLCIPHER_PBKDF2_HMAC_SHA1_LABEL, P4_TRANSIENT);
      } else if(default_kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA256) {
        sqlcipher_vdbe_return_string(pParse, "cipher_default_kdf_algorithm", SQLCIPHER_PBKDF2_HMAC_SHA256_LABEL, P4_TRANSIENT);
      } else if(default_kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA512) {
        sqlcipher_vdbe_return_string(pParse, "cipher_default_kdf_algorithm", SQLCIPHER_PBKDF2_HMAC_SHA512_LABEL, P4_TRANSIENT);
      }
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_compatibility")==0 ){
    if(ctx) {
      if(zRight) {
        int version = atoi(zRight); 

        switch(version) {
          case 1: 
            rc = sqlcipher_codec_ctx_set_pagesize(ctx, 1024);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_hmac_algorithm(ctx, SQLCIPHER_HMAC_SHA1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_kdf_algorithm(ctx, SQLCIPHER_PBKDF2_HMAC_SHA1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_kdf_iter(ctx, 4000); 
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_use_hmac(ctx, 0);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            break;

          case 2: 
            rc = sqlcipher_codec_ctx_set_pagesize(ctx, 1024);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_hmac_algorithm(ctx, SQLCIPHER_HMAC_SHA1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_kdf_algorithm(ctx, SQLCIPHER_PBKDF2_HMAC_SHA1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_kdf_iter(ctx, 4000); 
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_use_hmac(ctx, 1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            break;

          case 3:
            rc = sqlcipher_codec_ctx_set_pagesize(ctx, 1024);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_hmac_algorithm(ctx, SQLCIPHER_HMAC_SHA1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_kdf_algorithm(ctx, SQLCIPHER_PBKDF2_HMAC_SHA1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_kdf_iter(ctx, 64000); 
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_use_hmac(ctx, 1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            break;

          default:
            rc = sqlcipher_codec_ctx_set_pagesize(ctx, 4096);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_hmac_algorithm(ctx, SQLCIPHER_HMAC_SHA512);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_kdf_algorithm(ctx, SQLCIPHER_PBKDF2_HMAC_SHA512);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_kdf_iter(ctx, 256000); 
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            rc = sqlcipher_codec_ctx_set_use_hmac(ctx, 1);
            if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
            break;
        }  

        rc = codec_set_btree_to_codec_pagesize(db, pDb, ctx);
        if (rc != SQLITE_OK) sqlcipher_codec_ctx_set_error(ctx, SQLITE_ERROR);
      } 
    }
  }else 
  if( sqlite3_stricmp(zLeft,"cipher_default_compatibility")==0 ){
    if(zRight) {
      int version = atoi(zRight); 
      switch(version) {
        case 1: 
          default_page_size = 1024;
          default_hmac_algorithm = SQLCIPHER_HMAC_SHA1;
          default_kdf_algorithm = SQLCIPHER_PBKDF2_HMAC_SHA1;
          default_kdf_iter = 4000;
          sqlcipher_set_default_use_hmac(0);
          break;

        case 2: 
          default_page_size = 1024;
          default_hmac_algorithm = SQLCIPHER_HMAC_SHA1;
          default_kdf_algorithm = SQLCIPHER_PBKDF2_HMAC_SHA1;
          default_kdf_iter = 4000;
          sqlcipher_set_default_use_hmac(1);
          break;

        case 3:
          default_page_size = 1024;
          default_hmac_algorithm = SQLCIPHER_HMAC_SHA1;
          default_kdf_algorithm = SQLCIPHER_PBKDF2_HMAC_SHA1;
          default_kdf_iter = 64000;
          sqlcipher_set_default_use_hmac(1);
          break;

        default:
          default_page_size = 4096;
          default_hmac_algorithm = SQLCIPHER_HMAC_SHA512;
          default_kdf_algorithm = SQLCIPHER_PBKDF2_HMAC_SHA512;
          default_kdf_iter = 256000;
          sqlcipher_set_default_use_hmac(1);
          break;
      }  
    } 
  }else 
  if( sqlite3_stricmp(zLeft,"cipher_memory_security")==0 ){
    if( zRight ) {
      if(sqlite3GetBoolean(zRight,1)) {
        /* memory security can only be enabled, not disabled */
        sqlcipher_log(SQLCIPHER_LOG_DEBUG, SQLCIPHER_LOG_CORE, "sqlcipher_set_mem_security: on");
        sqlcipher_mem_security_on = 1;
      }
    } else {
      /* only report that memory security is enabled if pragma cipher_memory_security is ON and
         SQLCipher's allocator/deallocator was run at least one time */
      int state = sqlcipher_mem_security_on && sqlcipher_mem_executed;
      char *on = sqlite3_mprintf("%d", state);
      sqlcipher_log(SQLCIPHER_LOG_DEBUG, SQLCIPHER_LOG_CORE,
        "sqlcipher_get_mem_security: sqlcipher_mem_security_on = %d, sqlcipher_mem_executed = %d", 
        sqlcipher_mem_security_on, sqlcipher_mem_executed);
      sqlcipher_vdbe_return_string(pParse, "cipher_memory_security", on, P4_DYNAMIC);
    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_settings")==0 ){
    if(ctx) {
      int algorithm;
      char *pragma;

      pragma = sqlite3_mprintf("PRAGMA kdf_iter = %d;", ctx->kdf_iter);
      sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

      pragma = sqlite3_mprintf("PRAGMA cipher_page_size = %d;", ctx->page_sz);
      sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

      pragma = sqlite3_mprintf("PRAGMA cipher_use_hmac = %d;", SQLCIPHER_FLAG_GET(ctx->flags, CIPHER_FLAG_HMAC));
      sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

      pragma = sqlite3_mprintf("PRAGMA cipher_plaintext_header_size = %d;", ctx->plaintext_header_sz);
      sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

      algorithm = ctx->hmac_algorithm;
      pragma = NULL;
      if(algorithm == SQLCIPHER_HMAC_SHA1) {
        pragma = sqlite3_mprintf("PRAGMA cipher_hmac_algorithm = %s;", SQLCIPHER_HMAC_SHA1_LABEL);
      } else if(algorithm == SQLCIPHER_HMAC_SHA256) {
        pragma = sqlite3_mprintf("PRAGMA cipher_hmac_algorithm = %s;", SQLCIPHER_HMAC_SHA256_LABEL);
      } else if(algorithm == SQLCIPHER_HMAC_SHA512) {
        pragma = sqlite3_mprintf("PRAGMA cipher_hmac_algorithm = %s;", SQLCIPHER_HMAC_SHA512_LABEL);
      }
      sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

      algorithm = ctx->kdf_algorithm;
      pragma = NULL;
      if(algorithm == SQLCIPHER_PBKDF2_HMAC_SHA1) {
        pragma = sqlite3_mprintf("PRAGMA cipher_kdf_algorithm = %s;", SQLCIPHER_PBKDF2_HMAC_SHA1_LABEL);
      } else if(algorithm == SQLCIPHER_PBKDF2_HMAC_SHA256) {
        pragma = sqlite3_mprintf("PRAGMA cipher_kdf_algorithm = %s;", SQLCIPHER_PBKDF2_HMAC_SHA256_LABEL);
      } else if(algorithm == SQLCIPHER_PBKDF2_HMAC_SHA512) {
        pragma = sqlite3_mprintf("PRAGMA cipher_kdf_algorithm = %s;", SQLCIPHER_PBKDF2_HMAC_SHA512_LABEL);
      }
      sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

    }
  }else
  if( sqlite3_stricmp(zLeft,"cipher_default_settings")==0 ){
    char *pragma;

    pragma = sqlite3_mprintf("PRAGMA cipher_default_kdf_iter = %d;", default_kdf_iter);
    sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

    pragma = sqlite3_mprintf("PRAGMA cipher_default_page_size = %d;", default_page_size);
    sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

    pragma = sqlite3_mprintf("PRAGMA cipher_default_use_hmac = %d;", SQLCIPHER_FLAG_GET(default_flags, CIPHER_FLAG_HMAC)); 
    sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

    pragma = sqlite3_mprintf("PRAGMA cipher_default_plaintext_header_size = %d;", default_plaintext_header_size);
    sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

    pragma = NULL;
    if(default_hmac_algorithm == SQLCIPHER_HMAC_SHA1) {
      pragma = sqlite3_mprintf("PRAGMA cipher_default_hmac_algorithm = %s;", SQLCIPHER_HMAC_SHA1_LABEL);
    } else if(default_hmac_algorithm == SQLCIPHER_HMAC_SHA256) {
      pragma = sqlite3_mprintf("PRAGMA cipher_default_hmac_algorithm = %s;", SQLCIPHER_HMAC_SHA256_LABEL);
    } else if(default_hmac_algorithm == SQLCIPHER_HMAC_SHA512) {
      pragma = sqlite3_mprintf("PRAGMA cipher_default_hmac_algorithm = %s;", SQLCIPHER_HMAC_SHA512_LABEL);
    }
    sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);

    pragma = NULL;
    if(default_kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA1) {
      pragma = sqlite3_mprintf("PRAGMA cipher_default_kdf_algorithm = %s;", SQLCIPHER_PBKDF2_HMAC_SHA1_LABEL);
    } else if(default_kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA256) {
      pragma = sqlite3_mprintf("PRAGMA cipher_default_kdf_algorithm = %s;", SQLCIPHER_PBKDF2_HMAC_SHA256_LABEL);
    } else if(default_kdf_algorithm == SQLCIPHER_PBKDF2_HMAC_SHA512) {
      pragma = sqlite3_mprintf("PRAGMA cipher_default_kdf_algorithm = %s;", SQLCIPHER_PBKDF2_HMAC_SHA512_LABEL);
    }
    sqlcipher_vdbe_return_string(pParse, "pragma", pragma, P4_DYNAMIC);
  }else
  if( sqlite3_stricmp(zLeft,"cipher_integrity_check")==0 ){
    if(ctx) {
      sqlcipher_codec_ctx_integrity_check(ctx, pParse, "cipher_integrity_check");
    }
  } else
  if( sqlite3_stricmp(zLeft, "cipher_log_level")==0 ){
    if(zRight) {
      sqlcipher_log_level = SQLCIPHER_LOG_NONE;
      if(sqlite3_stricmp(zRight,      "ERROR")==0) sqlcipher_log_level = SQLCIPHER_LOG_ERROR;
      else if(sqlite3_stricmp(zRight, "WARN" )==0) sqlcipher_log_level = SQLCIPHER_LOG_WARN;
      else if(sqlite3_stricmp(zRight, "INFO" )==0) sqlcipher_log_level = SQLCIPHER_LOG_INFO;
      else if(sqlite3_stricmp(zRight, "DEBUG")==0) sqlcipher_log_level = SQLCIPHER_LOG_DEBUG;
      else if(sqlite3_stricmp(zRight, "TRACE")==0) sqlcipher_log_level = SQLCIPHER_LOG_TRACE;
    }
    sqlcipher_vdbe_return_string(pParse, "cipher_log_level", sqlcipher_get_log_level_str(sqlcipher_log_level), P4_TRANSIENT);
  } else
  if( sqlite3_stricmp(zLeft, "cipher_log_source")==0 ){
    if(zRight) {
      if(sqlite3_stricmp(zRight,      "NONE"    )==0) sqlcipher_log_source = SQLCIPHER_LOG_NONE;
      else if(sqlite3_stricmp(zRight, "ANY"     )==0) sqlcipher_log_source = SQLCIPHER_LOG_ANY;
      else {
        if(sqlite3_stricmp(zRight,      "CORE"    )==0) SQLCIPHER_FLAG_SET(sqlcipher_log_source, SQLCIPHER_LOG_CORE);
        else if(sqlite3_stricmp(zRight, "MEMORY"  )==0) SQLCIPHER_FLAG_SET(sqlcipher_log_source, SQLCIPHER_LOG_MEMORY);
        else if(sqlite3_stricmp(zRight, "MUTEX"   )==0) SQLCIPHER_FLAG_SET(sqlcipher_log_source, SQLCIPHER_LOG_MUTEX);
        else if(sqlite3_stricmp(zRight, "PROVIDER")==0) SQLCIPHER_FLAG_SET(sqlcipher_log_source, SQLCIPHER_LOG_PROVIDER);
      }
    }
    sqlcipher_vdbe_return_string(pParse, "cipher_log_source", sqlcipher_get_log_sources_str(sqlcipher_log_source), P4_DYNAMIC);
  } else
  if( sqlite3_stricmp(zLeft, "cipher_log")== 0 && zRight ){
      char *status = sqlite3_mprintf("%d", sqlcipher_set_log(zRight));
      sqlcipher_vdbe_return_string(pParse, "cipher_log", status, P4_DYNAMIC);
  }else {
    return 0;
  }
  return 1;
}
```
