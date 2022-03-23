import mistune
import os
import textwrap
import yaml
import json

from mistune.plugins import plugin_table
from base64 import b64encode
from urllib.parse import urlparse

YAML_BOUNDARY = '---'


def parse(post_path):
    """Parses the metadata and content from the provided post.

    Arguments:
        post_path {str} -- The absolute path to the Markdown post
    """
    raw_yaml = ''
    markdown = ''
    in_yaml = True
    with open(post_path, 'r') as post:
        for line in post.readlines():
            # Check if this is the ending tag
            if line.strip() == YAML_BOUNDARY:
                if in_yaml and raw_yaml:
                    in_yaml = False
                    continue
            if in_yaml:
                raw_yaml += line
            else:
                markdown += line
    front_matter = yaml.load(raw_yaml, Loader=yaml.SafeLoader)
    markdown = markdown.strip()
    return front_matter, markdown


def convtoconf(markdown, front_matter={}):
    if front_matter is None:
        front_matter = {}

    author_keys = front_matter.get('author_keys', [])
    renderer = ConfluenceRenderer(authors=author_keys)
    markdown_html = mistune.create_markdown(renderer=renderer, plugins=[plugin_table])
    page_html = markdown_html(markdown)

    title = front_matter.get('title', renderer.top_heading)

    return page_html, renderer.attachments, title

class ConfluenceRenderer(mistune.HTMLRenderer):
    def __init__(self, authors=[]):
        self.attachments = []
        if authors is None:
            authors = []
        self.authors = authors
        self.has_toc = False
        self.top_heading = None
        super().__init__()

    def layout(self, content):
        """Renders the final layout of the content. This includes a two-column
        layout, with the authors and ToC on the left, and the content on the
        right.

        The layout looks like this:

        ------------------------------------------
        |             |                          |
        |             |                          |
        | Sidebar     |         Content          |
        | (30% width) |      (800px width)       |
        |             |                          |
        ------------------------------------------

        Arguments:
            content {str} -- The HTML of the content
        """
        toc = textwrap.dedent('''
            <h1>Table of Contents</h1>
            <p><ac:structured-macro ac:name="toc" ac:schema-version="1">
                <ac:parameter ac:name="exclude">^(Authors|Table of Contents)$</ac:parameter>
            </ac:structured-macro></p>''')
        # Ignore the TOC if we haven't processed any headers to avoid making a
        # blank one
        if not self.has_toc:
            toc = ''
        authors = self.render_authors()
        column = textwrap.dedent('''
            <ac:structured-macro ac:name="column" ac:schema-version="1">
                <ac:parameter ac:name="width">{width}</ac:parameter>
                <ac:rich-text-body>{content}</ac:rich-text-body>
            </ac:structured-macro>''')
        sidebar = column.format(width='30%', content=toc + authors)
        main_content = column.format(width='800px', content=content)
        return sidebar + main_content

    def heading(self, text, level):
        """Processes a Markdown header.

        In our case, this just tells us that we need to render a TOC. We don't
        actually do any special rendering for headers.
        """
        self.has_toc = True

        # use the first h1 as the title and don't render it because confluence shows the title itself
        if self.top_heading is None and level == 1:
            self.top_heading = text
            return ""

        return super().heading(text, level)

    def render_authors(self):
        """Renders a header that details which author(s) published the post.

        This is used since Confluence will show the post published as our
        service account.

        Arguments:
            author_keys {str} -- The Confluence user keys for each post author

        Returns:
            str -- The HTML to prepend to the post specifying the authors
        """
        author_template = '''<ac:structured-macro ac:name="profile-picture" ac:schema-version="1">
                <ac:parameter ac:name="User"><ri:user ri:userkey="{user_key}" /></ac:parameter>
            </ac:structured-macro>&nbsp;
            <ac:link><ri:user ri:userkey="{user_key}" /></ac:link>'''
        author_content = '<br />'.join(
            author_template.format(user_key=user_key)
            for user_key in self.authors)
        return '<h1>Authors</h1><p>{}</p>'.format(author_content)

    def block_mermaid(self, code):
        """Render mermaid code block as an png image using the mermaid.ink service

        In the future the image could be downloaded and attached as an internal image

        Arguments:
          code {str} -- The contents of the mermaid code block

        Returns:
          str -- The HTML to render a mermaid diagram as a png image
        """
        data = b64encode(json.dumps({ "code": code, "mermaid": { "theme": "default" } }).encode())
        src = "https://mermaid.ink/img/%s" % data.decode("utf-8")
        tag_template = '<ac:image>{image_tag}</ac:image>'
        image_tag = '<ri:url ri:value="{}" />'.format(src)
        return tag_template.format(image_tag=image_tag)

    def block_quote(self, content):
        if content.count("</p>") == 1:
            stripped = content.replace("<p>", "").replace("</p>", "")
        else:
            stripped = content

        return super().block_quote(stripped)

    def block_code(self, code, lang=None):
        if lang == "mermaid":
            return self.block_mermaid(code)

        return textwrap.dedent('''\
            <ac:structured-macro ac:name="code" ac:schema-version="1">
                <ac:parameter ac:name="language">{l}</ac:parameter>
                <ac:plain-text-body><![CDATA[{c}]]></ac:plain-text-body>
            </ac:structured-macro>
        ''').format(c=code, l=lang or '')

    def image(self, src, title, alt_text):
        """Renders an image into XHTML expected by Confluence.

        Arguments:
            src {str} -- The path to the image
            title {str} -- The title attribute for the image
            alt_text {str} -- The alt text for the image

        Returns:
            str -- The constructed XHTML tag
        """
        # Check if the image is externally hosted, or hosted as a static
        # file within Journal
        is_external = bool(urlparse(src).netloc)
        tag_template = '<ac:image>{image_tag}</ac:image>'
        image_tag = '<ri:url ri:value="{}" />'.format(src)
        if not is_external:
            image_tag = '<ri:attachment ri:filename="{}" />'.format(
                os.path.basename(src))
            self.attachments.append(src)
        return tag_template.format(image_tag=image_tag)

    def link(self, link, title, content):
        """Render a link into HTML
        if the link is external leave it as is, if internal the
        might be best to turn it into text for now as the links won't work.

        Can't think of how to fix them correctly because ancestors have to be
        created first, so the posts for the links in the root article won't exist
        yet. Normal wikis this isn't a problem, but confluence puts the id in the
        path for posts. :(

        Arguments:
            link {str} -- the href for the link
            title {str} -- the title of the link
            content {str} -- the innerHTML for the link

        Returns:
            str -- the html for the link (or text)
        """
        is_external = bool(urlparse(link).netloc)
        if is_external:
            return super().link(link, title, content)

        return super().text(content)
