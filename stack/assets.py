import os

from troposphere import GetAtt, Equals, If, Join, Output, Parameter, Ref, Split, iam
from troposphere.cloudfront import (
    DefaultCacheBehavior,
    Distribution,
    DistributionConfig,
    ForwardedValues,
    Origin,
    S3Origin,
    ViewerCertificate
)
from troposphere.s3 import (
    Bucket,
    CorsConfiguration,
    CorsRules,
    Private,
    PublicRead,
    VersioningConfiguration
)

from .common import arn_prefix
from .domain import domain_name, domain_name_alternates, no_alt_domains
from .template import template

common_bucket_conf = dict(
    VersioningConfiguration=VersioningConfiguration(
        Status="Enabled"
    ),
    DeletionPolicy="Retain",
    CorsConfiguration=CorsConfiguration(
        CorsRules=[CorsRules(
            AllowedOrigins=Split(";", Join("", [
                "https://", domain_name,
                If(
                    no_alt_domains,
                    # if we don't have any alternate domains, return an empty string
                    "",
                    # otherwise, return the ';https://' that will be needed by the first domain
                    ";https://",
                ),
                # then, add all the alternate domains, joined together with ';https://'
                Join(";https://", domain_name_alternates),
                # now that we have a string of origins separated by ';', Split() is used to make it into a list again
            ])),
            AllowedMethods=[
                "POST",
                "PUT",
                "HEAD",
                "GET",
            ],
            AllowedHeaders=[
                "*",
            ],
        )],
    ),
)

# Create an S3 bucket that holds statics and media
assets_bucket = template.add_resource(
    Bucket(
        "AssetsBucket",
        AccessControl=PublicRead,
        **common_bucket_conf,
    )
)


# Output S3 asset bucket name
template.add_output(Output(
    "AssetsBucketDomainName",
    Description="Assets bucket domain name",
    Value=GetAtt(assets_bucket, "DomainName")
))


# Create an S3 bucket that holds user uploads or other non-public files
private_assets_bucket = template.add_resource(
    Bucket(
        "PrivateAssetsBucket",
        AccessControl=Private,
        **common_bucket_conf,
    )
)


# Output S3 asset bucket name
template.add_output(Output(
    "PrivateAssetsBucketDomainName",
    Description="Private assets bucket domain name",
    Value=GetAtt(private_assets_bucket, "DomainName")
))


# central asset management policy for use in instance roles
assets_management_policy = iam.Policy(
    PolicyName="AssetsManagementPolicy",
    PolicyDocument=dict(
        Statement=[
            dict(
                Effect="Allow",
                Action=["s3:ListBucket"],
                Resource=Join("", [arn_prefix, ":s3:::", Ref(assets_bucket)]),
            ),
            dict(
                Effect="Allow",
                Action=["s3:*"],
                Resource=Join("", [arn_prefix, ":s3:::", Ref(assets_bucket), "/*"]),
            ),
            dict(
                Effect="Allow",
                Action=["s3:ListBucket"],
                Resource=Join("", [arn_prefix, ":s3:::", Ref(private_assets_bucket)]),
            ),
            dict(
                Effect="Allow",
                Action=["s3:*"],
                Resource=Join("", [arn_prefix, ":s3:::", Ref(private_assets_bucket), "/*"]),
            ),
        ],
    ),
)


if os.environ.get('USE_GOVCLOUD') != 'on':
    # Allow alternate CNAMEs for CloudFront distributions
    distribution_aliases = Ref(template.add_parameter(Parameter(
        "DistributionAliases",
        Description="A comma-separated list of CNAMEs (alternate domain names), if any, for the "
                    "CloudFront distribution, e.g. static.mydomain.com",
        Type="CommaDelimitedList",
    )))

    # Create a CloudFront CDN distribution
    distribution = template.add_resource(
        Distribution(
            'AssetsDistribution',
            DistributionConfig=DistributionConfig(
                Aliases=distribution_aliases,
                Origins=[Origin(
                    Id="Assets",
                    DomainName=GetAtt(assets_bucket, "DomainName"),
                    S3OriginConfig=S3Origin(
                        OriginAccessIdentity="",
                    ),
                )],
                DefaultCacheBehavior=DefaultCacheBehavior(
                    TargetOriginId="Assets",
                    ForwardedValues=ForwardedValues(
                        # Cache results *should* vary based on querystring (e.g., 'style.css?v=3')
                        QueryString=True,
                        # make sure headers needed by CORS policy above get through to S3
                        # http://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/header-caching.html#header-caching-web-cors
                        Headers=[
                            'Origin',
                            'Access-Control-Request-Headers',
                            'Access-Control-Request-Method',
                        ],
                    ),
                    ViewerProtocolPolicy="allow-all",
                ),
                Enabled=True
            ),
        )
    )

    media_distribution_alias = Ref(template.add_parameter(Parameter(
        "MediaDistributionAlias",
        Description="Optional CNAME (alternate domain name) for the PrivateAssetsBucket's "
                    "CloudFront distribution, e.g. media.mydomain.com",
        Type="String",
    )))
    no_media_distribution_alias = "NoMediaDistributionAlias"
    template.add_condition(
        no_media_distribution_alias,
        Equals(media_distribution_alias, ""),
    )

    # Currently, you can specify only certificates that are in the US East (N. Virginia) region.
    # http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-cloudfront-distributionconfig-viewercertificate.html
    media_acm_certificate_arn = Ref(template.add_parameter(Parameter(
        "MediaAcmCertificateArn",
        Description="If you're using MediaDistributionAlias, enter the Amazon Resource Name (ARN) "
                    "of an AWS Certificate Manager (ACM) certificate from US East (N. Virginia).",
        Type="String",
    )))
    no_media_acm_certificate_arn = "NoMediaDistributionAlias"
    template.add_condition(
        no_media_acm_certificate_arn,
        Equals(media_acm_certificate_arn, ""),
    )

    media_distribution = template.add_resource(
        Distribution(
            'MediaAssetsDistribution',
            DistributionConfig=DistributionConfig(
                Aliases=If(no_media_distribution_alias, Ref("AWS::NoValue"), [media_distribution_alias]),
                Origins=[Origin(
                    Id="Assets",
                    DomainName=GetAtt(private_assets_bucket, "DomainName"),
                    S3OriginConfig=S3Origin(
                        OriginAccessIdentity="",
                    ),
                )],
                DefaultCacheBehavior=DefaultCacheBehavior(
                    TargetOriginId="Assets",
                    ForwardedValues=ForwardedValues(
                        # Cache results *should* vary based on querystring (e.g., 'style.css?v=3')
                        QueryString=True,
                        # make sure headers needed by CORS policy above get through to S3
                        # http://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/header-caching.html#header-caching-web-cors
                        Headers=[
                            'Origin',
                            'Access-Control-Request-Headers',
                            'Access-Control-Request-Method',
                        ],
                    ),
                    ViewerProtocolPolicy="allow-all",
                ),
                Enabled=True,
                ViewerCertificate=If(
                                        no_media_acm_certificate_arn,
                                        Ref("AWS::NoValue"),
                                        ViewerCertificate(
                                            AcmCertificateArn=media_acm_certificate_arn,
                                            SslSupportMethod='sni-only',
                                        )
                ),
            ),
        )
    )

    # Output CloudFront url
    template.add_output(Output(
        "AssetsDistributionDomainName",
        Description="The assest CDN domain name",
        Value=GetAtt(distribution, "DomainName")
    ))
else:
    distribution = None
