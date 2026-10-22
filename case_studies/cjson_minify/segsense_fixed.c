/* Real-world bug: cJSON_Minify from cJSON v1.7.10 (https://github.com/DaveGamble/cJSON),
 * copied unchanged except for the CJSON_PUBLIC export macro. It has two published
 * out-of-bounds bugs, CVE-2019-11835 (unterminated comment) and CVE-2019-11834
 * (unterminated string ending in a backslash), fixed upstream in v1.7.11/v1.7.12.
 * main() is a small reproducer. See case_studies/cjson_minify/README.md.
 */
/*
  Copyright (c) 2009-2017 Dave Gamble and cJSON contributors

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in
  all copies or substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
  THE SOFTWARE.
*/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void cJSON_Minify(char *json)
{
    unsigned char *into = (unsigned char*)json;

    if (json == NULL)
    {
        return;
    }

    while (*json)
    {
        if (*json == ' ')
        {
            json++;
        }
        else if (*json == '\t')
        {
            /* Whitespace characters. */
            json++;
        }
        else if (*json == '\r')
        {
            json++;
        }
        else if (*json=='\n')
        {
            json++;
        }
        else if ((*json == '/') && (json[1] == '/'))
        {
            /* double-slash comments, to end of line. */
            while (*json && (*json != '\n'))
            {
                json++;
            }
        }
        else if ((*json == '/') && (json[1] == '*'))
        {
            /* multiline comments. */
            while (*json && !((*json == '*') && (json[1] == '/')))
            {
                json++;
            }
            if (*json) {
                json += 2;
            }
        }
        else if (*json == '\"')
        {
            /* string literals, which are \" sensitive. */
            *into++ = (unsigned char)*json++;
            while (*json && (*json != '\"'))
            {
                if (*json == '\\')
                {
                    *into++ = (unsigned char)*json++;
                    if (*json) {
                        *into++ = (unsigned char)*json++;
                    } else {
                        break;
                    }
                }
                else
                {
                    *into++ = (unsigned char)*json++;
                }
            }
            if (*json == '\"') {
                *into++ = (unsigned char)*json++;
            }
        }
        else
        {
            /* All other characters. */
            *into++ = (unsigned char)*json++;
        }
    }

    /* and null-terminate. */
    *into = '\0';
}

static void run(const char *label, const char *input)
{
    /* Exactly sized heap copy, like a JSON document read from a file or socket. */
    char *buf = malloc(strlen(input) + 1);
    strcpy(buf, input);
    cJSON_Minify(buf);
    printf("%s: [%s]\n", label, buf);
    free(buf);
}

int main(void)
{
    run("valid", "{ \"name\" : \"cJSON\", // a comment\n  \"tags\": [1, 2] /* another */ }");
    run("escapes", "{\"path\": \"C:\\\\tmp\", \"quote\": \"say \\\"hi\\\"\"}");
    run("unterminated comment", "{\"a\": 1} /* never closed");
    run("unterminated string", "{\"a\": \"oops\\");
    return 0;
}
